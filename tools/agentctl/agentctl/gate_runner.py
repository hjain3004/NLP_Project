"""Run contract checks ("gates") under a hard deadline with no surviving descendants.

Public API:

* :func:`run_checks` -- run shell commands one at a time in ``cwd`` and return one
  result dict per command.
* :func:`integrator_hardening` -- context manager for the process that runs gates:
  non-dumpable (``PR_SET_DUMPABLE=0``) and a child subreaper
  (``PR_SET_CHILD_SUBREAPER``) while it is active, restored afterwards.
  :func:`run_checks` enters it itself; it is re-entrant.
* :func:`scrubbed_environment` -- the allow-listed environment every gate gets.

Each gate:

* gets an environment built from an explicit allow-list (never inherited);
* runs in its own session and process group, stdin from ``/dev/null``, stdout and
  stderr to files (never pipes), under ``RLIMIT_FSIZE`` (default 64 MiB),
  ``RLIMIT_CORE=0`` and a bounded ``RLIMIT_NPROC``;
* when ``unshare --user --map-root-user --pid --fork --mount-proc --kill-child``
  demonstrably works on this host (probed once, cached), runs inside fresh user, PID
  and mount namespaces. It then sees only its own processes in ``/proc``, so it cannot
  read the integrator's ``environ``, ``cwd`` or open files. Everything in the
  namespace dies with the namespace init. The evidence records
  ``gate_isolation: "user_pid_namespace"``, or ``"none"`` when the fallback path ran;
* has one hard deadline. On expiry the process group is killed, then every
  descendant: found by walking parent-pid chains in ``/proc/*/stat`` (orphans
  reparent to this process because it is a subreaper), plus any process carrying
  the gate's marker variable;
* fails with ``reason: "gate_left_processes"`` if anything it started is still
  alive when its shell exits, even after exit 0. Those processes are killed;
* fails with ``reason: "output_limit_exceeded"`` if it reached the file-size limit.

RESIDUAL RISK, stated plainly: this is NOT an OS sandbox. A gate runs as the
integrator's uid and can read and write anything that uid can, by absolute path,
including the repository and the event store. The namespaces hide processes; they
do not restrict the filesystem. ``--map-root-user`` makes the gate see itself as
uid 0 inside its namespace, which is visible to the gate (recorded in the
evidence). Without unshare (``gate_isolation: "none"``), a gate running as root
with full capabilities can still read the integrator's ``/proc`` entries despite
``PR_SET_DUMPABLE=0``. This is the ``unsandboxed_tools`` class of AGENTS.md
section 7: postcondition checks only, globally serialized, secret-free (callers
must not hand secrets to gates, and the allow-list never does), and prohibited
for high-risk tasks. The integrator therefore also verifies afterwards that the
repository was not tampered with. ``RLIMIT_NPROC`` is not enforced by Linux for
uid 0. Descendant tracking assumes no other thread of this process starts child
processes while a gate runs: children that existed before the gate started are
never touched.
"""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_LIMITS",
    "GATE_ID_VARIABLE",
    "drop_sensitive_environment",
    "integrator_hardening",
    "namespace_isolation_available",
    "run_checks",
    "scrubbed_environment",
]

LINUX = sys.platform.startswith("linux")
GATE_ID_VARIABLE = "AGENTCTL_GATE_ID"
_PATH_FALLBACK = "/usr/local/bin:/usr/bin:/bin"

DEFAULT_LIMITS: dict[str, Any] = {
    "file_size_bytes": 64 * 1024 * 1024,
    "extra_processes": 512,
    "kill_grace_seconds": 2.0,
    #: "auto" uses the user+PID namespace when the probe succeeds; "none" never does.
    "isolation": "auto",
}

_PR_GET_DUMPABLE, _PR_SET_DUMPABLE = 3, 4
_PR_SET_CHILD_SUBREAPER, _PR_GET_CHILD_SUBREAPER = 36, 37

_UNSHARE = ("--user", "--map-root-user", "--pid", "--fork", "--mount-proc", "--kill-child")
# PID 1 inside the namespace: run the gate as a child, then count what is still alive
# (non-zombie) besides itself, using shell builtins only, and write "<rc> <left>".
_NAMESPACE_INIT = (
    '"$0" -c "$1"; rc=$?; n=0; '
    'for p in /proc/[0-9]*; do '
    '[ "$p" = /proc/1 ] && continue; '
    'line=; read -r line < "$p/stat" 2>/dev/null || continue; '
    'case "$line" in *") Z "*) ;; *) n=$((n+1));; esac; '
    'done; printf "%s %s\\n" "$rc" "$n" > "$2"; exit "$rc"'
)


# ------------------------------------------------------------- hardening ----

_libc: Any = None
_hardening_lock = threading.Lock()
_hardening_depth = 0
_hardening_saved: tuple[int, int] | None = None


def _prctl() -> Any:
    global _libc
    if not LINUX:
        return None
    if _libc is None:
        try:
            _libc = ctypes.CDLL(None, use_errno=True)
            _libc.prctl  # noqa: B018 - presence check
        except (OSError, AttributeError):
            _libc = False
    return _libc or None


def _get_state(libc: Any) -> tuple[int, int]:
    reaper = ctypes.c_int(0)
    libc.prctl(_PR_GET_CHILD_SUBREAPER, ctypes.byref(reaper), 0, 0, 0)
    return int(libc.prctl(_PR_GET_DUMPABLE, 0, 0, 0, 0)), reaper.value


@contextlib.contextmanager
def integrator_hardening() -> Iterator[dict[str, Any]]:
    """Make this process non-dumpable and a child subreaper while active (Linux only).

    Non-dumpable: other processes of the same uid without ``CAP_SYS_PTRACE`` can no
    longer read this process's ``/proc/<pid>/environ``, ``cwd`` or ``fd`` links.
    Subreaper: descendants orphaned by a gate reparent to this process, so they can
    be found and killed. Both are restored when the outermost use exits.
    """
    global _hardening_depth, _hardening_saved
    libc = _prctl()
    status = {"non_dumpable": False, "child_subreaper": False, "platform": sys.platform}
    if libc is None:
        yield status
        return
    with _hardening_lock:
        if _hardening_depth == 0:
            _hardening_saved = _get_state(libc)
            libc.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0)
            libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
        _hardening_depth += 1
        dumpable, reaper = _get_state(libc)
    status["non_dumpable"] = dumpable == 0
    status["child_subreaper"] = reaper == 1
    try:
        yield status
    finally:
        with _hardening_lock:
            _hardening_depth -= 1
            if _hardening_depth == 0 and _hardening_saved is not None:
                libc.prctl(_PR_SET_CHILD_SUBREAPER, _hardening_saved[1], 0, 0, 0)
                libc.prctl(_PR_SET_DUMPABLE, _hardening_saved[0], 0, 0, 0)
                _hardening_saved = None


# ------------------------------------------------------------ namespaces ----

_namespace_probe: bool | None = None


def namespace_isolation_available() -> bool:
    """True when the user+PID namespace wrapper demonstrably works here (probed once)."""
    global _namespace_probe
    if _namespace_probe is None:
        _namespace_probe = False
        unshare = shutil.which("unshare", path=_PATH_FALLBACK)
        if LINUX and unshare:
            try:
                done = subprocess.run(
                    [unshare, *_UNSHARE, "/bin/sh", "-c",
                     'test "$$" = 1 && test "$(ls -d /proc/[0-9]* | wc -l)" -le 4 && echo isolated'],
                    env={"PATH": _PATH_FALLBACK}, stdin=subprocess.DEVNULL,
                    capture_output=True, text=True, timeout=10,
                )
                _namespace_probe = done.returncode == 0 and done.stdout.strip() == "isolated"
            except (OSError, subprocess.SubprocessError):
                _namespace_probe = False
    return _namespace_probe


# ----------------------------------------------------------- environment ----


def scrubbed_environment(home: str) -> dict[str, str]:
    """A minimal allow-listed environment: nothing is inherited from the caller."""
    path_entries = [str(Path(sys.executable).parent), *_PATH_FALLBACK.split(":")]
    return {
        "PATH": ":".join(dict.fromkeys(path_entries)),
        "HOME": home,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        # The gate tree is outside any repository; never let git search upwards.
        "GIT_CEILING_DIRECTORIES": "/",
    }


_SENSITIVE = re.compile(r"^(ANTHROPIC_|CLAUDE)|TOKEN|SECRET|KEY|PASSWORD|PASSWD|CREDENTIAL", re.IGNORECASE)


def drop_sensitive_environment() -> list[str]:
    """Remove credential-like variables (ANTHROPIC_*, CLAUDE*, *TOKEN*, *SECRET*, *KEY*, ...)
    from this process's ``os.environ`` and return their names (never their values).

    The integrator needs none of them. This does not rewrite the initial environment
    block the kernel shows in ``/proc/<pid>/environ``; gates are kept from reading that
    by the PID namespace (and non-dumpable mode), and never inherit ``os.environ``.
    """
    names = sorted(name for name in os.environ if _SENSITIVE.search(name))
    for name in names:
        del os.environ[name]
    return names


# ------------------------------------------------------------- processes ----


def _process_table() -> dict[int, tuple[int, str]]:
    """``pid -> (ppid, state)`` for every visible process (Linux ``/proc``)."""
    table: dict[int, tuple[int, str]] = {}
    try:
        names = os.listdir("/proc")
    except OSError:
        return table
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as handle:
                fields = handle.read().rsplit(b")", 1)[1].split()
        except (OSError, IndexError):
            continue
        try:
            table[int(name)] = (int(fields[1]), fields[0].decode())
        except (IndexError, ValueError):
            continue
    return table


def _marked_pids(gate_id: str) -> set[int]:
    needle = f"{GATE_ID_VARIABLE}={gate_id}".encode()
    found: set[int] = set()
    for pid in _process_table():
        if pid == os.getpid():
            continue
        try:
            with open(f"/proc/{pid}/environ", "rb") as handle:
                if needle in handle.read().split(b"\0"):
                    found.add(pid)
        except OSError:
            continue
    return found


def _gate_processes(gate_pid: int | None, before: set[int], gate_id: str) -> tuple[set[int], set[int]]:
    """(alive, zombie children of ours) that belong to this gate.

    ``gate_pid`` is passed only while the gate's shell is unreaped, so a recycled pid
    can never be mistaken for it.
    """
    me = os.getpid()
    table = _process_table()
    children: dict[int, list[int]] = {}
    for pid, (ppid, _state) in table.items():
        children.setdefault(ppid, []).append(pid)
    roots = {pid for pid in children.get(me, ()) if pid not in before}
    if gate_pid is not None:
        roots.add(gate_pid)
    seen: set[int] = set()
    stack = [pid for pid in roots if pid in table]
    while stack:
        pid = stack.pop()
        if pid in seen or pid == me:
            continue
        seen.add(pid)
        stack.extend(children.get(pid, ()))
    seen |= _marked_pids(gate_id) & set(table)
    alive = {pid for pid in seen if table.get(pid, (0, "Z"))[1] != "Z"}
    zombies = {pid for pid in seen - alive if table.get(pid, (0, ""))[0] == me and pid != gate_pid}
    return alive, zombies


def _reap(pids: set[int]) -> None:
    for pid in pids:
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass


def _sweep(process: subprocess.Popen[bytes], before: set[int], gate_id: str, grace: float) -> tuple[int, bool]:
    """Kill the gate's group and every descendant until none remain (bounded).

    Returns (number alive at the first look, whether everything is gone).
    """
    deadline = time.monotonic() + grace
    first: int | None = None
    while True:
        if not LINUX:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)
            return 0, True
        # Count before killing, so a descendant still in the group is counted as left behind.
        own = process.pid if process.returncode is None else None
        alive, zombies = _gate_processes(own, before, gate_id)
        alive.discard(process.pid)  # the shell itself is reaped by Popen, not counted
        if first is None:
            first = len(alive)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        for pid in alive:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        _reap(zombies)
        if not alive:
            return first, True
        if time.monotonic() >= deadline:
            return first, False
        time.sleep(0.01)


def _child_setup(limits: Mapping[str, Any], nproc: int | None) -> Any:
    file_size = int(limits["file_size_bytes"])

    def setup() -> None:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        _soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        cap = file_size if hard == resource.RLIM_INFINITY else min(file_size, hard)
        resource.setrlimit(resource.RLIMIT_FSIZE, (cap, cap))
        if nproc is not None:
            _soft, hard = resource.getrlimit(resource.RLIMIT_NPROC)
            cap = nproc if hard == resource.RLIM_INFINITY else min(nproc, hard)
            resource.setrlimit(resource.RLIMIT_NPROC, (cap, hard))

    return setup


def _process_budget(extra: int) -> int | None:
    if not LINUX:
        return None
    uid = os.getuid()
    count = 0
    for pid in _process_table():
        try:
            if os.stat(f"/proc/{pid}").st_uid == uid:
                count += 1
        except OSError:
            continue
    return count + extra


def _file_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def run_checks(
    commands: Sequence[str],
    cwd: str | os.PathLike[str],
    timeout: float,
    env_extra: Mapping[str, str] | None = None,
    hard_limits: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run each command in ``cwd`` (one hard deadline per command); see the module docstring."""
    limits = {**DEFAULT_LIMITS, **(hard_limits or {})}
    isolate = limits["isolation"] == "auto" and namespace_isolation_available()
    unshare = shutil.which("unshare", path=_PATH_FALLBACK) if isolate else None
    grace = float(limits["kill_grace_seconds"])
    results: list[dict[str, Any]] = []
    with integrator_hardening(), tempfile.TemporaryDirectory(prefix="agentctl-gate-scratch-") as scratch_name:
        scratch = Path(scratch_name)
        home = scratch / "agentctl-gate-home"
        home.mkdir()
        for index, command in enumerate(commands):
            gate_id = uuid.uuid4().hex
            environment = scrubbed_environment(str(home))
            environment.update(env_extra or {})
            environment[GATE_ID_VARIABLE] = gate_id
            stdout_path, stderr_path = scratch / f"{index}.stdout", scratch / f"{index}.stderr"
            status_path = scratch / f"{index}.status"
            if unshare:
                argv = [unshare, *_UNSHARE, "/bin/sh", "-c", _NAMESPACE_INIT, "/bin/sh", command, str(status_path)]
            else:
                argv = ["/bin/sh", "-c", command]
            before = {pid for pid, (ppid, _s) in _process_table().items() if ppid == os.getpid()}
            started = time.monotonic()
            deadline = started + timeout
            with open(stdout_path, "wb") as stdout_file, open(stderr_path, "wb") as stderr_file:
                process = subprocess.Popen(
                    argv,
                    cwd=cwd,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    start_new_session=True,
                    close_fds=True,
                    preexec_fn=_child_setup(limits, _process_budget(int(limits["extra_processes"]))),
                )
            timed_out = False
            try:
                process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
            left, cleared = _sweep(process, before, gate_id, grace)
            if timed_out:
                try:
                    process.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    pass  # never block on the child after the deadline
            exit_code = None if timed_out else process.returncode
            if unshare and not timed_out:
                try:
                    reported = status_path.read_text().split()
                    left = max(left, int(reported[1]))
                except (OSError, IndexError, ValueError):
                    pass  # the namespace init was killed or the gate removed the file
            stdout_hash, stdout_bytes = _file_digest(stdout_path)
            stderr_hash, stderr_bytes = _file_digest(stderr_path)
            limit_hit = max(stdout_bytes, stderr_bytes) >= int(limits["file_size_bytes"])
            reason = None
            if timed_out:
                reason = "timed_out"
            elif limit_hit:
                reason = "output_limit_exceeded"
            elif left or not cleared:
                reason = "gate_left_processes"
            elif exit_code != 0:
                reason = "exit_nonzero"
            results.append(
                {
                    "gate": command,
                    "result": "pass" if reason is None else "fail",
                    "reason": reason,
                    "exit_code": exit_code,
                    "timed_out": timed_out,
                    "left_processes": left,
                    "descendants_cleared": cleared,
                    "gate_isolation": "user_pid_namespace" if unshare else "none",
                    "stdout_hash": stdout_hash,
                    "stderr_hash": stderr_hash,
                    "stdout_bytes": stdout_bytes,
                    "stderr_bytes": stderr_bytes,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }
            )
    return results
