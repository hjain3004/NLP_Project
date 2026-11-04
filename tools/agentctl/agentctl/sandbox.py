"""Run one shell command for a candidate check inside an enforced runc sandbox.

Why this exists. Candidate-controlled checks used to run at the control-plane uid with
absolute-path reach to the event store, credentials, other worktrees and the shared ``.git``,
and with network access. PID namespaces, environment filtering and after-the-fact tamper
hashes are not a boundary. This module is one: a daemon-free OCI bundle executed by ``runc``.

What a sandboxed command can see and do (the contract, enforced by the kernel):

* **Filesystem.** A fresh root filesystem built in a private temporary directory outside the
  repository, mounted read-only. Mounted into it: the host runtime subset ``/usr``, ``/lib``,
  ``/lib64``, ``/bin``, ``/sbin`` (as they exist; read-only), ``/etc/alternatives`` (read-only
  symlink farm, only so ``/usr/bin`` links resolve), the check tree at ``/work`` (read-write,
  owned by the sandbox uid), a size-bounded tmpfs ``/tmp``, ``/proc`` of its own PID namespace,
  and a minimal tmpfs ``/dev`` and ``/dev/shm``. ``/etc/passwd`` and ``/etc/group`` are tiny
  files generated here (no host data). **Nothing else exists**: no repository checkout, no
  ``agent-system/runtime`` store, no ``~/.claude`` or ``~/.cashbooks``, no ``/root`` or
  ``/home``, no container-runtime sockets, no shared ``.git``, no other worktree.
* **Identity.** uid/gid 65534, no capabilities (all five sets empty), ``noNewPrivileges``,
  a seccomp filter that refuses ``unshare``/``setns``/``mount``/``ptrace``/``bpf``/``keyctl``/...
  and ``clone`` with any ``CLONE_NEW*`` flag (``clone3`` answers ENOSYS so libc falls back).
* **Network.** A new network namespace with no interface but loopback and no route: no external
  connection can be made and the host's ``127.0.0.1`` is a different, unreachable namespace.
* **Processes.** New PID, IPC, UTS and mount namespaces. When the command's shell exits (or the
  deadline passes) the PID namespace's init dies and the kernel kills every straggler, however
  it daemonised. OCI rlimits bound open files, processes, file size (core dumps are off). Memory,
  pids and CPU cgroup limits are applied only where the probe shows they work on this host;
  ``limits_enforced`` in the probe result and in every run states exactly which.
* **Output.** The command's stdout and stderr go to files opened by the parent (never pipes);
  the file-size rlimit bounds them.

What it does NOT cover (stated, not hidden): a kernel or runc vulnerability; the host runtime
directories are readable (``/usr`` etc. contain no secrets on a clean host, but they are the
host's); runc itself runs as root; disk consumption of the check tree and CPU time are bounded
only by the deadline and the tree's filesystem; the Docker backend is not implemented.

``probe()`` proves, with a canary run, that the boundary works on this host; ``run_in_sandbox``
refuses to run when it does not (``SandboxUnavailable``). Nothing in this module falls back to
running a command unsandboxed.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import resource
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_LIMITS",
    "SANDBOX_GID",
    "SANDBOX_UID",
    "SandboxUnavailable",
    "build_config",
    "probe",
    "run_in_sandbox",
    "runc_binary",
    "state_root",
]

SANDBOX_UID = 65534
SANDBOX_GID = 65534
WORK_DIRECTORY = "/work"
RUNC_ENVIRONMENT_VARIABLE = "AGENTCTL_RUNC"
STATE_ROOT_ENVIRONMENT_VARIABLE = "AGENTCTL_RUNC_ROOT"

#: Resource limits applied to every run. ``memory_bytes``/``pids``/``cpu_quota_percent`` become
#: cgroup limits only where the probe proves the host accepts them.
DEFAULT_LIMITS: dict[str, Any] = {
    "file_size_bytes": 128 * 1024 * 1024,
    "open_files": 4096,
    "processes": 1024,
    "tmp_bytes": 256 * 1024 * 1024,
    "memory_bytes": 4 * 1024 * 1024 * 1024,
    "pids": 1024,
    "cpu_quota_percent": 400,
    "capture_bytes": 4096,
    "kill_grace_seconds": 5.0,
}

#: Host runtime directories bound read-only, in this order, when they exist.
_RUNTIME_DIRECTORIES = ("/usr", "/lib", "/lib64", "/bin", "/sbin")
_ALTERNATIVES = "/etc/alternatives"
_ALLOWED_ROOT_ENTRIES = frozenset(
    {"bin", "dev", "etc", "lib", "lib64", "proc", "sbin", "tmp", "usr", "work"}
)
_RUNC_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_ID_CHARS = frozenset("0123456789abcdef")

_DENIED_SYSCALLS = (
    "unshare", "setns", "mount", "umount", "umount2", "pivot_root", "chroot", "ptrace",
    "process_vm_readv", "process_vm_writev", "kexec_load", "kexec_file_load", "init_module",
    "finit_module", "delete_module", "create_module", "bpf", "perf_event_open", "userfaultfd",
    "keyctl", "add_key", "request_key", "open_by_handle_at", "name_to_handle_at", "swapon",
    "swapoff", "reboot", "acct", "settimeofday", "clock_settime", "clock_adjtime", "adjtimex",
    "iopl", "ioperm", "mount_setattr", "move_mount", "open_tree", "fsopen", "fsconfig",
    "fsmount", "fspick", "syslog", "lookup_dcookie", "quotactl", "quotactl_fd", "nfsservctl",
    "vm86", "vm86old", "modify_ldt", "pciconfig_read", "pciconfig_write", "uselib", "ustat",
    "sysfs", "_sysctl", "kcmp", "pidfd_getfd", "landlock_create_ruleset", "mbind",
    "set_mempolicy", "migrate_pages", "move_pages",
)
#: ``CLONE_NEWNS|NEWCGROUP|NEWUTS|NEWIPC|NEWUSER|NEWPID|NEWNET``: one rule per bit so any of them denies.
_CLONE_NAMESPACE_FLAGS = (
    0x00020000, 0x02000000, 0x04000000, 0x08000000, 0x10000000, 0x20000000, 0x40000000,
)

_SECCOMP_ARCHITECTURES = {
    "x86_64": ["SCMP_ARCH_X86_64", "SCMP_ARCH_X86", "SCMP_ARCH_X32"],
    "aarch64": ["SCMP_ARCH_AARCH64", "SCMP_ARCH_ARM"],
}


class SandboxUnavailable(RuntimeError):
    """The sandbox cannot be established on this host; no command may run unsandboxed."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


# ----------------------------------------------------------------- runc ----


def runc_binary() -> str | None:
    """The runc executable: ``$AGENTCTL_RUNC`` (a nonexistent path means unavailable) or ``PATH``."""
    configured = os.environ.get(RUNC_ENVIRONMENT_VARIABLE)
    if configured:
        return configured if os.access(configured, os.X_OK) and os.path.isfile(configured) else None
    return shutil.which("runc", path=_RUNC_PATH)


def state_root() -> str:
    """Directory holding runc's container state (``runc --root``), private to this user."""
    configured = os.environ.get(STATE_ROOT_ENVIRONMENT_VARIABLE)
    if configured:
        path = configured
    elif os.geteuid() == 0 and os.path.isdir("/run") and os.access("/run", os.W_OK):
        path = "/run/agentctl-runc"
    else:
        path = os.path.join(tempfile.gettempdir(), f"agentctl-runc-{os.geteuid()}")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def _runc_env() -> dict[str, str]:
    return {"PATH": _RUNC_PATH, "LANG": "C"}


def _container_id() -> str:
    return "agentctl-chk-" + uuid.uuid4().hex[:16]


def _safe_environment(env: Mapping[str, str]) -> list[str]:
    for name, value in env.items():
        if not name or "=" in name or "\0" in name or "\0" in str(value):
            raise ValueError(f"invalid environment entry: {name!r}")
    return [f"{name}={value}" for name, value in env.items()]


# ---------------------------------------------------------------- bundle ----


def _runtime_layout() -> tuple[list[dict[str, Any]], list[tuple[str, str]], list[str]]:
    """(bind mounts, symlinks to create in the root fs, runtime directories present)."""
    mounts: list[dict[str, Any]] = []
    links: list[tuple[str, str]] = []
    present: list[str] = []
    for directory in _RUNTIME_DIRECTORIES:
        if os.path.islink(directory):
            target = os.readlink(directory)
            resolved = os.path.realpath(directory)
            # usr-merge hosts: /bin -> usr/bin etc. Recreate the link; the target is mounted via /usr.
            if resolved.startswith("/usr/") and not os.path.isabs(target) and not target.startswith(".."):
                links.append((directory, target))
                present.append(directory)
                continue
        if os.path.isdir(directory):
            mounts.append(_bind(directory, directory, "ro"))
            present.append(directory)
    if os.path.isdir(_ALTERNATIVES):
        mounts.append(_bind(_ALTERNATIVES, _ALTERNATIVES, "ro"))
    return mounts, links, present


def _bind(source: str, destination: str, mode: str) -> dict[str, Any]:
    options = ["rbind", mode, "rprivate", "nosuid"]
    if mode == "ro":
        options.append("nodev")
    return {"destination": destination, "type": "bind", "source": source, "options": options}


def _seccomp_profile() -> dict[str, Any]:
    deny = {"action": "SCMP_ACT_ERRNO", "errnoRet": 1}
    rules: list[dict[str, Any]] = [{"names": list(_DENIED_SYSCALLS), **deny}]
    rules.append({"names": ["clone3"], "action": "SCMP_ACT_ERRNO", "errnoRet": 38})
    for flag in _CLONE_NAMESPACE_FLAGS:
        rules.append(
            {
                "names": ["clone"], **deny,
                "args": [{"index": 0, "value": flag, "valueTwo": flag, "op": "SCMP_CMP_MASKED_EQ"}],
            }
        )
    profile: dict[str, Any] = {"defaultAction": "SCMP_ACT_ALLOW", "syscalls": rules}
    architectures = _SECCOMP_ARCHITECTURES.get(platform.machine())
    if architectures:
        profile["architectures"] = architectures
    return profile


def build_config(
    command: str,
    env: Mapping[str, str],
    tree_dir: str,
    limits: Mapping[str, Any],
    *,
    cgroup_limits: Mapping[str, bool],
) -> dict[str, Any]:
    """The OCI runtime configuration (``config.json``) for one command."""
    bind_mounts, _links, _present = _runtime_layout()
    tmp_bytes = int(limits["tmp_bytes"])
    mounts: list[dict[str, Any]] = [
        {"destination": "/proc", "type": "proc", "source": "proc",
         "options": ["nosuid", "noexec", "nodev"]},
        {"destination": "/dev", "type": "tmpfs", "source": "tmpfs",
         "options": ["nosuid", "strictatime", "mode=755", "size=1m"]},
        {"destination": "/dev/shm", "type": "tmpfs", "source": "shm",
         "options": ["nosuid", "noexec", "nodev", "mode=1777", "size=64m"]},
        *bind_mounts,
        {**_bind(tree_dir, WORK_DIRECTORY, "rw"), "options": ["rbind", "rw", "rprivate", "nosuid", "nodev"]},
        {"destination": "/tmp", "type": "tmpfs", "source": "tmpfs",
         "options": ["nosuid", "nodev", "mode=1777", f"size={tmp_bytes}"]},
    ]
    nofile, nproc = int(limits["open_files"]), int(limits["processes"])
    fsize = int(limits["file_size_bytes"])
    rlimits = [
        {"type": "RLIMIT_NOFILE", "hard": nofile, "soft": nofile},
        {"type": "RLIMIT_NPROC", "hard": nproc, "soft": nproc},
        {"type": "RLIMIT_FSIZE", "hard": fsize, "soft": fsize},
        {"type": "RLIMIT_CORE", "hard": 0, "soft": 0},
    ]
    resources: dict[str, Any] = {"devices": [{"allow": False, "access": "rwm"}]}
    if cgroup_limits.get("memory"):
        resources["memory"] = {"limit": int(limits["memory_bytes"])}
    if cgroup_limits.get("pids"):
        resources["pids"] = {"limit": int(limits["pids"])}
    if cgroup_limits.get("cpu"):
        period = 100000
        resources["cpu"] = {"quota": period * int(limits["cpu_quota_percent"]) // 100, "period": period}
    empty = {name: [] for name in ("bounding", "effective", "inheritable", "permitted", "ambient")}
    return {
        "ociVersion": "1.0.2",
        "process": {
            "terminal": False,
            "user": {"uid": SANDBOX_UID, "gid": SANDBOX_GID, "additionalGids": []},
            "args": ["/bin/sh", "-c", command],
            "env": _safe_environment(env),
            "cwd": WORK_DIRECTORY,
            "capabilities": empty,
            "rlimits": rlimits,
            "noNewPrivileges": True,
        },
        "root": {"path": "rootfs", "readonly": True},
        "hostname": "agentctl-check",
        "mounts": mounts,
        "linux": {
            "resources": resources,
            "namespaces": [
                {"type": "pid"}, {"type": "network"}, {"type": "ipc"}, {"type": "uts"},
                {"type": "mount"},
            ],
            "seccomp": _seccomp_profile(),
            "maskedPaths": [
                "/proc/acpi", "/proc/asound", "/proc/kcore", "/proc/keys", "/proc/latency_stats",
                "/proc/timer_list", "/proc/timer_stats", "/proc/sched_debug", "/sys/firmware",
                "/proc/scsi",
            ],
            "readonlyPaths": ["/proc/bus", "/proc/fs", "/proc/irq", "/proc/sys", "/proc/sysrq-trigger"],
        },
    }


def _prepare_rootfs(rootfs: Path, config: Mapping[str, Any]) -> None:
    """Empty mount points, usr-merge links, and generated ``/etc/passwd``/``/etc/group``."""
    rootfs.mkdir(mode=0o755)
    _bind_mounts, links, _present = _runtime_layout()
    for mount in config["mounts"]:
        (rootfs / str(mount["destination"]).lstrip("/")).mkdir(parents=True, exist_ok=True)
    for name, target in links:
        (rootfs / name.lstrip("/")).symlink_to(target)
    etc = rootfs / "etc"
    etc.mkdir(exist_ok=True)
    (etc / "passwd").write_text(
        f"root:x:0:0:root:/nonexistent:/bin/false\nnobody:x:{SANDBOX_UID}:{SANDBOX_GID}:nobody:/tmp:/bin/false\n"
    )
    (etc / "group").write_text(f"root:x:0:\nnogroup:x:{SANDBOX_GID}:\n")
    for path in (etc / "passwd", etc / "group"):
        path.chmod(0o644)


def _chown_tree(root: str) -> None:
    """Give the check tree to the sandbox uid (``lchown``: never follows a symlink)."""
    if os.geteuid() != 0:
        raise SandboxUnavailable("sandbox_requires_root", "runc is run as root so it can set up namespaces")
    os.lchown(root, SANDBOX_UID, SANDBOX_GID)
    for directory, subdirectories, files in os.walk(root, followlinks=False):
        for name in (*subdirectories, *files):
            with contextlib.suppress(FileNotFoundError):
                os.lchown(os.path.join(directory, name), SANDBOX_UID, SANDBOX_GID)


def _mounts_under(path: str) -> list[str]:
    prefix = os.path.realpath(path)
    found: list[str] = []
    try:
        with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                fields = line.split()
                if len(fields) > 4:
                    point = fields[4].replace("\\040", " ")
                    if point == prefix or point.startswith(prefix + "/"):
                        found.append(point)
    except OSError:
        pass
    return found


# ---------------------------------------------------------------- running ----


def _excerpt(path: Path, size: int) -> str:
    """The last ``size`` bytes of a file, decoded leniently (never reads the whole file)."""
    if size <= 0:
        return ""
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        handle.seek(max(0, handle.tell() - size))
        return handle.read(size).decode("utf-8", errors="replace")


def _output_size(*paths: Path) -> int:
    return max((os.stat(path).st_size for path in paths if path.exists()), default=0)


def _runc_limits(file_size: int) -> Any:
    """``preexec_fn`` for the runc process: a hard file-size ceiling on everything runc writes.

    runc, not the container, writes the container's output to our files (the container's stdio are pipes
    to runc), so the container's own ``RLIMIT_FSIZE`` does not bound them; this does.
    """
    def setup() -> None:
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_size, file_size))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return setup


def _digest(path: Path) -> tuple[str, int]:
    digest, total = hashlib.sha256(), 0
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


def _runc(runc: str, *args: str, timeout: float = 20.0) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [runc, "--root", state_root(), *args], env=_runc_env(), stdin=subprocess.DEVNULL,
        capture_output=True, timeout=timeout, check=False,
    )


def _destroy_container(runc: str, container_id: str) -> bool:
    """Kill every process of the container and delete it. True when no trace remains."""
    for _attempt in range(3):
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            _runc(runc, "kill", container_id, "KILL")
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            _runc(runc, "delete", "--force", container_id)
        try:
            listing = _runc(runc, "list", "-q")
        except (OSError, subprocess.SubprocessError):
            return False
        if container_id not in listing.stdout.decode(errors="replace").split():
            return True
        time.sleep(0.1)
    return False


def _cleanup_bundle(bundle: Path) -> bool:
    if _mounts_under(str(bundle)):
        return False  # something is still mounted in there; deleting could reach host files
    shutil.rmtree(bundle, ignore_errors=True)
    return not bundle.exists()


def _execute(
    runc: str,
    command: str,
    env: Mapping[str, str],
    tree_dir: str,
    timeout: float,
    out_dir: Path,
    limits: Mapping[str, Any],
    cgroup_limits: Mapping[str, bool],
    label: str,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{label or 'run'}-{uuid.uuid4().hex[:8]}"
    stdout_path, stderr_path = out_dir / f"{stem}.stdout", out_dir / f"{stem}.stderr"
    container_id = _container_id()
    bundle = Path(tempfile.mkdtemp(prefix="agentctl-sandbox-"))
    started = time.monotonic()
    timed_out = False
    exit_code: int | None = None
    started_in_sandbox = False
    limit_hit = False
    runtime_error = ""
    destroyed = False
    process: subprocess.Popen[bytes] | None = None
    try:
        _chown_tree(tree_dir)
        config = build_config(command, env, tree_dir, limits, cgroup_limits=cgroup_limits)
        _prepare_rootfs(bundle / "rootfs", config)
        (bundle / "config.json").write_text(json.dumps(config, sort_keys=True))
        pid_file = bundle / "container.pid"
        cap = int(limits["file_size_bytes"])
        with open(stdout_path, "wb") as stdout_file, open(stderr_path, "wb") as stderr_file:
            process = subprocess.Popen(
                [runc, "--root", state_root(), "--log", str(bundle / "runc.log"), "--log-format", "json",
                 "run", "--bundle", str(bundle), "--pid-file", str(pid_file), container_id],
                cwd="/", env=_runc_env(), stdin=subprocess.DEVNULL, stdout=stdout_file,
                stderr=stderr_file, start_new_session=True, close_fds=True,
                preexec_fn=_runc_limits(cap),
            )
        deadline = time.monotonic() + timeout
        while True:
            try:
                process.wait(timeout=max(0.0, min(0.2, deadline - time.monotonic())))
                break
            except subprocess.TimeoutExpired:
                pass
            limit_hit = _output_size(stdout_path, stderr_path) >= cap
            timed_out = not limit_hit and time.monotonic() >= deadline
            if limit_hit or timed_out:
                destroyed = _destroy_container(runc, container_id)
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(process.pid, signal.SIGKILL)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=float(limits["kill_grace_seconds"]))
                break
        started_in_sandbox = pid_file.exists()
        if process.returncode is not None and not timed_out:
            exit_code = process.returncode
        if not started_in_sandbox and not timed_out:
            log = bundle / "runc.log"
            runtime_error = (log.read_text(errors="replace")[-800:] if log.exists() else "") or _excerpt(
                stderr_path, 400
            )
    finally:
        if process is not None and process.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)
            with contextlib.suppress(subprocess.SubprocessError):
                process.wait(timeout=5)
        destroyed = _destroy_container(runc, container_id) or destroyed
        cleaned = _cleanup_bundle(bundle)
    capture = int(limits["capture_bytes"])
    stdout_hash, stdout_bytes = _digest(stdout_path)
    stderr_hash, stderr_bytes = _digest(stderr_path)
    limit_hit = limit_hit or max(stdout_bytes, stderr_bytes) >= int(limits["file_size_bytes"])
    result: dict[str, Any] = {
        "exit_code": 124 if timed_out else 153 if limit_hit else exit_code,  # 153 = 128 + SIGXFSZ
        "timed_out": timed_out,
        "started": started_in_sandbox,
        "output_limit_exceeded": limit_hit,
        "stdout_hash": stdout_hash,
        "stderr_hash": stderr_hash,
        "stdout_bytes": stdout_bytes,
        "stderr_bytes": stderr_bytes,
        "stdout_excerpt": _excerpt(stdout_path, capture),
        "stderr_excerpt": _excerpt(stderr_path, capture) if started_in_sandbox else "",
        "duration_ms": int((time.monotonic() - started) * 1000),
        "container_id": container_id,
        "container_deleted": destroyed,
        "bundle_removed": cleaned,
    }
    if not started_in_sandbox and not timed_out:
        result["runtime_error"] = runtime_error
    return result


def _mounts_summary(tree_dir: str) -> list[dict[str, str]]:
    summary = [{"destination": "/proc", "kind": "proc"}, {"destination": "/dev", "kind": "tmpfs"},
               {"destination": "/dev/shm", "kind": "tmpfs"}]
    for mount in build_config("true", {}, tree_dir, DEFAULT_LIMITS, cgroup_limits={})["mounts"]:
        if mount["type"] == "bind":
            mode = "rw" if "rw" in mount["options"] else "ro"
            source = "<check tree>" if mount["destination"] == WORK_DIRECTORY else mount["source"]
            summary.append({"destination": mount["destination"], "kind": f"bind-{mode}", "source": source})
    summary.append({"destination": "/tmp", "kind": "tmpfs"})
    return summary


def run_in_sandbox(
    command: str,
    *,
    tree_dir: str | os.PathLike[str],
    timeout: float,
    out_dir: str | os.PathLike[str],
    limits: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    label: str = "",
) -> dict[str, Any]:
    """Run ``/bin/sh -c command`` in ``/work`` (the contents of ``tree_dir``) inside the sandbox.

    Returns ``exit_code`` (124 and ``timed_out`` on a deadline, like ``timeout(1)``; when ``started`` is
    false the sandbox itself failed and ``exit_code`` is runc's status, with ``runtime_error``), SHA-256 hashes and sizes
    of stdout and stderr (the full output is in files under ``out_dir``), bounded excerpts,
    ``duration_ms``, ``isolation: "runc"``, ``network: "none"``, ``mounts`` and ``limits_enforced``.

    Raises :class:`SandboxUnavailable` when the probe fails; the command is then not run.
    """
    status = probe()
    if not status["available"]:
        raise SandboxUnavailable(str(status["reason"]), str(status.get("detail", "")))
    merged = {**DEFAULT_LIMITS, **(limits or {})}
    tree = os.path.realpath(tree_dir)
    if not os.path.isdir(tree):
        raise ValueError("tree_dir is not a directory")
    runc = str(status["runc"])
    result = _execute(
        runc, command, dict(env or {}), tree, float(timeout), Path(out_dir), merged,
        status["limits_enforced"], label,
    )
    result.update(
        {
            "isolation": "runc",
            "network": "none",
            "mounts": _mounts_summary(tree),
            "limits_enforced": dict(status["limits_enforced"]),
            "command": command,
        }
    )
    return result


# ------------------------------------------------------------------ probe ----

_probe_lock = threading.Lock()
_probe_cache: dict[str, dict[str, Any]] = {}

_PROBE_SCRIPT = r"""
exec 2>&1
[ "$(id -u)" != 0 ] || { echo "FAIL runs-as-root"; exit 11; }
[ ! -e "$CANARY" ] || { echo "FAIL host-canary-visible"; exit 12; }
for entry in /*; do
  case "${entry#/}" in bin|dev|etc|lib|lib64|proc|sbin|tmp|usr|work) ;; *) echo "FAIL unexpected-root-entry ${entry}"; exit 13;; esac
done
[ -d /root ] && { echo "FAIL root-dir-visible"; exit 14; }
[ -d /home ] && { echo "FAIL home-dir-visible"; exit 14; }
[ -e /var/run/docker.sock ] && { echo "FAIL docker-socket-visible"; exit 15; }
if ( : > /usr/agentctl-write-probe ) 2>/dev/null; then echo "FAIL usr-writable"; exit 16; fi
if ( : > /etc/agentctl-write-probe ) 2>/dev/null; then echo "FAIL etc-writable"; exit 16; fi
( : > /work/agentctl-write-probe ) 2>/dev/null || { echo "FAIL work-not-writable"; exit 17; }
rm -f /work/agentctl-write-probe
others=$(sed -n '3,$p' /proc/net/dev | cut -d: -f1 | tr -d ' ' | grep -v '^lo$' | grep -c . )
[ "$others" = 0 ] || { echo "FAIL network-interfaces-present"; exit 18; }
routes=$(sed -n '2,$p' /proc/net/route | grep -c .)
[ "$routes" = 0 ] || { echo "FAIL network-routes-present"; exit 19; }
if command -v python3 >/dev/null 2>&1; then
  python3 - <<'PY' || { echo "FAIL network-reachable"; exit 20; }
import os, socket, sys
# A listener the host process holds on ITS 127.0.0.1, and a TEST-NET address: neither may be reachable.
for target in (("127.0.0.1", int(os.environ["PROBE_PORT"])), ("192.0.2.1", 9)):
    try:
        socket.create_connection(target, timeout=1).close()
    except OSError:
        continue
    sys.exit(1)
sys.exit(0)
PY
fi
if command -v unshare >/dev/null 2>&1 && unshare --user true 2>/dev/null; then echo "FAIL user-namespace-allowed"; exit 21; fi
echo "RUNTIME git=$(git --version 2>/dev/null || echo none)"
echo "RUNTIME python=$(python3 -c 'import sys;print(sys.version.split()[0])' 2>/dev/null || echo none)"
echo "RUNTIME arch=$(uname -m)"
echo PROBE-OK
"""


def _probe_once(
    runc: str, cgroup_limits: Mapping[str, bool], canary: str, tree: str, out: Path, port: int
) -> dict[str, Any]:
    return _execute(
        runc, _PROBE_SCRIPT,
        {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "CANARY": canary, "PROBE_PORT": str(port)},
        tree, 30.0, out, DEFAULT_LIMITS, cgroup_limits, "probe",
    )


def _unavailable(reason: str, detail: str = "", **extra: Any) -> dict[str, Any]:
    return {"available": False, "reason": reason, "detail": detail[:400], **extra}


def _run_probe(runc: str | None) -> dict[str, Any]:
    if runc is None:
        return _unavailable("runc_not_found", f"set {RUNC_ENVIRONMENT_VARIABLE} or install runc")
    if not platform.system() == "Linux":
        return _unavailable("sandbox_requires_linux", platform.system(), runc=runc)
    if os.geteuid() != 0:
        return _unavailable("sandbox_requires_root", "runc is run as root", runc=runc)
    try:
        version_run = subprocess.run(
            [runc, "--version"], env=_runc_env(), capture_output=True, text=True, timeout=10, check=False
        )
        version = (version_run.stdout.splitlines() or ["unknown"])[0]
        scratch = Path(tempfile.mkdtemp(prefix="agentctl-probe-"))
    except (OSError, subprocess.SubprocessError) as exc:
        return _unavailable("runc_not_executable", type(exc).__name__, runc=runc)
    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        port = listener.getsockname()[1]
        canary = scratch / "host-canary.secret"
        canary.write_text("agentctl-synthetic-canary\n")
        tree = scratch / "tree"
        tree.mkdir()
        want = {"memory": True, "pids": True, "cpu": True}
        outcome = _probe_once(runc, want, str(canary), str(tree), scratch / "out", port)
        enforced = dict(want)
        if not outcome["started"]:
            # Find out which cgroup limits this host accepts; the limits it refuses are reported as
            # not enforced rather than silently claimed.
            enforced = {"memory": False, "pids": False, "cpu": False}
            for name in ("memory", "pids", "cpu"):
                single = {**enforced, name: True}
                trial = _probe_once(runc, single, str(canary), str(tree), scratch / "out", port)
                if trial["started"]:
                    enforced[name] = True
            outcome = _probe_once(runc, enforced, str(canary), str(tree), scratch / "out", port)
        text = (outcome.get("stdout_excerpt") or "")
        if not outcome["started"]:
            return _unavailable(
                "runc_cannot_start_container", str(outcome.get("runtime_error", ""))[-300:],
                runc=runc, runc_version=version,
            )
        if outcome["exit_code"] != 0 or "PROBE-OK" not in text:
            failure = next((line for line in text.splitlines() if line.startswith("FAIL")), text[-200:])
            return _unavailable("sandbox_boundary_check_failed", failure, runc=runc, runc_version=version)
        runtime_lines = sorted(line for line in text.splitlines() if line.startswith("RUNTIME "))
        runtime = {"runc": version, "lines": runtime_lines, "seccomp": True}
        runtime_hash = hashlib.sha256(json.dumps(runtime, sort_keys=True).encode()).hexdigest()
        return {
            "available": True, "reason": "ok", "runc": runc, "runc_version": version,
            "limits_enforced": {
                "rlimits": True, "seccomp": True, "capabilities": "none",
                "memory": enforced["memory"], "pids": enforced["pids"], "cpu": enforced["cpu"],
            },
            "runtime": runtime, "runtime_hash": runtime_hash,
        }
    finally:
        listener.close()
        shutil.rmtree(scratch, ignore_errors=True)


def probe(refresh: bool = False) -> dict[str, Any]:
    """Prove the sandbox works on this host with a canary run; cached per runc path. Never raises.

    The canary run asserts, from inside the container: non-root uid; a planted host-only canary
    file path is not visible; the root directory holds only the expected entries; ``/root``,
    ``/home`` and the Docker socket are absent; ``/usr`` and ``/etc`` are not writable while
    ``/work`` is; the only network interface is loopback with no route, a listener the probing process holds
    on its own ``127.0.0.1`` cannot be connected to and neither can a TEST-NET address; a user namespace
    cannot be created. Returns ``available``,
    ``reason`` (``ok`` or a stable code), ``limits_enforced`` and a ``runtime_hash``.
    """
    try:
        runc = runc_binary()
        key = f"{runc}|{os.environ.get(STATE_ROOT_ENVIRONMENT_VARIABLE, '')}"
        with _probe_lock:
            if refresh or key not in _probe_cache:
                try:
                    _probe_cache[key] = _run_probe(runc)
                except Exception as exc:  # noqa: BLE001 - probe must never raise
                    _probe_cache[key] = _unavailable("probe_error", f"{type(exc).__name__}: {exc}", runc=runc)
            return dict(_probe_cache[key])
    except Exception as exc:  # noqa: BLE001
        return _unavailable("probe_error", f"{type(exc).__name__}: {exc}")
