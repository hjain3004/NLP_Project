"""The one check-environment contract that both ``verify`` and ``integrate`` run candidate checks in.

Contract ``agentctl.check-environment`` version 1 (also described in
``docs/architecture/CHECK_ENVIRONMENT.md``). A contract-owned deterministic check is a shell command.
Whatever stage runs it, against whichever commit, it sees exactly this:

* **Tree.** The commit's tree materialised byte for byte from Git objects into a fresh temporary directory
  outside the repository (no filters, no attributes, no ``.git``, symlinks never followed, duplicate and
  clashing entries refused), mounted read-write at ``/work``.
* **Sandbox.** ``runc`` (see :mod:`agentctl.sandbox`): uid 65534, no capabilities, no network, a private
  PID namespace, a read-only host runtime subset, a private ``/tmp``; nothing of the control plane exists
  inside it. The command line is ``/bin/sh -c <check>`` with working directory ``/work``.
* **Environment variables.** Only ``PATH``, ``HOME=/tmp``, ``LANG``, ``TZ``, ``PYTHONDONTWRITEBYTECODE``
  and ``AGENTCTL_CANDIDATE_COMMIT``. ``PYTHONPATH`` is never set: a check that needs it says so in its own
  command (``PYTHONPATH=tools/agentctl python3 -m unittest ...``).
* **Git.** Inside the sandbox, before the first check, a PRIVATE synthetic repository is created in
  ``/work``: ``git init``, a fixed identity and dates, ``git add -A -f``, one commit
  ``check-environment snapshot of <commit>``. ``git rev-parse --show-toplevel``, ``git ls-files`` and
  ``git status`` therefore work, from the materialised tree only; it holds no control-plane metadata. Its
  ``HEAD`` hash is intentionally NOT the real commit's hash (it is a different commit of the same files);
  the real hash is exported as ``AGENTCTL_CANDIDATE_COMMIT`` so a check can compare.
* **Limits and vocabulary.** One deadline per check. A deadline is exit code ``124`` with
  ``timed_out: true`` and ``reason: "timed_out"``; a non-zero exit is ``reason: "exit_nonzero"``; output
  past the file-size limit is ``"output_limit_exceeded"``. A process a check leaves behind cannot outlive
  its container: the PID namespace dies with the check's shell, so there is nothing to report.

The only alternative is ``AGENTCTL_CHECK_ISOLATION=legacy-unsafe``: the previous unsandboxed runner,
selected explicitly, recorded as ``isolation: "legacy-unsafe"`` in the evidence, and refused for any task
whose risk is not ``low`` or ``medium``. If the sandbox is unavailable (``probe()`` fails) nothing runs and
the caller refuses with reason ``sandbox_unavailable``; there is no silent fallback.

:func:`run_candidate_checks` is the single entry point. ``Scheduler.verify_candidate`` calls it with the
candidate's result commit; the integrator's affected-gate rerun calls it with the planned integration
commit. The returned ``environment`` is identical for identical host, checks settings and timeout.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from . import sandbox
from .errors import PermissionViolation

__all__ = [
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "DEFAULT_CHECK_TIMEOUT_SECONDS",
    "ENV_ALLOWLIST",
    "ISOLATION_LEGACY",
    "ISOLATION_RUNC",
    "ISOLATION_VARIABLE",
    "CandidateTreeError",
    "CandidateTreeUnreadable",
    "CandidateTreeUnsafe",
    "CheckEnvironmentUnavailable",
    "ObjectReader",
    "legacy_environment",
    "materialize_tree",
    "require_sandbox",
    "run_candidate_checks",
    "selected_isolation",
]

CONTRACT_ID = "agentctl.check-environment"
CONTRACT_VERSION = 1
ISOLATION_VARIABLE = "AGENTCTL_CHECK_ISOLATION"
ISOLATION_RUNC = "runc"
ISOLATION_LEGACY = "legacy-unsafe"
SANDBOX_UNAVAILABLE = "sandbox_unavailable"
ISOLATION_INVALID = "check_isolation_invalid"
DEFAULT_CHECK_TIMEOUT_SECONDS = 600.0
SNAPSHOT_TIMEOUT_SECONDS = 120.0
ENV_ALLOWLIST = (
    "AGENTCTL_CANDIDATE_COMMIT", "HOME", "LANG", "PATH", "PYTHONDONTWRITEBYTECODE", "TZ",
)
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SNAPSHOT_COMMAND = (
    "git init -q -b main --template= . "
    "&& git config user.name 'agentctl check-environment' "
    "&& git config user.email 'check-environment@agentctl.invalid' "
    "&& git config commit.gpgsign false && git config gc.auto 0 && git config core.autocrlf false "
    "&& git add -A -f "
    "&& GIT_AUTHOR_DATE='2000-01-01T00:00:00+0000' GIT_COMMITTER_DATE='2000-01-01T00:00:00+0000' "
    "git commit -q --no-verify --allow-empty "
    "-m \"check-environment snapshot of $AGENTCTL_CANDIDATE_COMMIT\""
)


# ------------------------------------------------------------- selection ----


class CheckEnvironmentUnavailable(PermissionViolation):
    """The requested isolation cannot be provided; no check may run. ``reason_code`` names why."""

    def __init__(self, message: str, *, reason_code: str, **context: object) -> None:
        super().__init__(message, reason_code=reason_code, **context)
        self.reason_code = reason_code


def selected_isolation() -> str:
    """``runc`` (default) or ``legacy-unsafe`` from ``AGENTCTL_CHECK_ISOLATION``; anything else is refused."""
    value = os.environ.get(ISOLATION_VARIABLE, "").strip()
    if value in ("", ISOLATION_RUNC):
        return ISOLATION_RUNC
    if value == ISOLATION_LEGACY:
        return ISOLATION_LEGACY
    raise CheckEnvironmentUnavailable(
        f"{ISOLATION_VARIABLE} must be '{ISOLATION_RUNC}' or '{ISOLATION_LEGACY}'",
        reason_code=ISOLATION_INVALID, value=value[:40],
    )


def require_sandbox() -> dict[str, Any]:
    """The sandbox probe result when it works; otherwise :class:`CheckEnvironmentUnavailable`."""
    status = sandbox.probe()
    if not status["available"]:
        raise CheckEnvironmentUnavailable(
            "candidate checks need the runc sandbox and it is unavailable; nothing was run",
            reason_code=SANDBOX_UNAVAILABLE, sandbox_reason=status["reason"],
            detail=str(status.get("detail", ""))[:200],
        )
    return status


def _contract(isolation: str, network: str, git_mode: str, tree: str, cwd: str, timeout: float) -> dict[str, Any]:
    return {
        "contract": CONTRACT_ID,
        "contract_version": CONTRACT_VERSION,
        "isolation": isolation,
        "network": network,
        "git_mode": git_mode,
        "tree": tree,
        "shell": "/bin/sh -c",
        "cwd": cwd,
        "env_allowlist": sorted(ENV_ALLOWLIST),
        "check_timeout_seconds": float(timeout),
    }


def legacy_environment(git_mode: str, *, timeout: float = DEFAULT_CHECK_TIMEOUT_SECONDS) -> dict[str, Any]:
    """The environment record of the explicitly selected unsandboxed runner (not the contract above)."""
    environment = _contract(
        ISOLATION_LEGACY, "host", git_mode,
        "leased-worktree" if git_mode == "shared-git-link" else "materialized-from-git-objects",
        "host-path", timeout,
    )
    environment.update(
        {"limits_enforced": {}, "runtime_hash": None, "warning": "unsandboxed: runs at the control-plane uid"}
    )
    return environment


def sandbox_environment(status: Mapping[str, Any], timeout: float) -> dict[str, Any]:
    environment = _contract(
        ISOLATION_RUNC, "none", "synthetic-repository", "materialized-from-git-objects", "/work", timeout
    )
    environment.update(
        {
            "user": f"{sandbox.SANDBOX_UID}:{sandbox.SANDBOX_GID}",
            "runtime_hash": status["runtime_hash"],
            "runtime": dict(status["runtime"]),
            "limits_enforced": dict(status["limits_enforced"]),
            "limits": {
                key: sandbox.DEFAULT_LIMITS[key]
                for key in ("file_size_bytes", "open_files", "processes", "tmp_bytes", "memory_bytes", "pids")
            },
        }
    )
    return environment


# ---------------------------------------------------------- the git side ----


class CandidateTreeError(Exception):
    """The commit's tree cannot be materialised safely."""


class CandidateTreeUnsafe(CandidateTreeError):
    def __init__(self, path: str, why: str) -> None:
        super().__init__(f"{why}: {path}")
        self.path, self.why = path, why


class CandidateTreeUnreadable(CandidateTreeError):
    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.context = dict(context)


class ObjectReader:
    """Read-only plumbing access to Git objects with no config, hooks, replace refs or credentials."""

    _HARDENING = (
        "--no-pager", "-c", "core.fsmonitor=false", "-c", "core.pager=cat", "-c", "diff.external=",
        "-c", "core.hooksPath=/dev/null", "-c", "core.attributesFile=/dev/null", "-c", "gc.auto=0",
        "-c", "maintenance.auto=false",
    )

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        git = shutil.which("git")
        path = ["/usr/local/bin", "/usr/bin", "/bin"]
        if git:
            path.insert(0, os.path.dirname(os.path.realpath(git)))
        self._env = {
            "PATH": os.pathsep.join(dict.fromkeys(path)), "HOME": "/nonexistent", "LC_ALL": "C",
            "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_REPLACE_OBJECTS": "1",
        }

    def _command(self, *args: str) -> list[str]:
        return ["git", *self._HARDENING, *args]

    def run(self, *args: str, text: bool = True, **_ignored: Any) -> "subprocess.CompletedProcess[Any]":
        return subprocess.run(
            self._command(*args), cwd=self.root, env=self._env, capture_output=True, text=text, check=False
        )

    def popen(self, *args: str, **_ignored: Any) -> "subprocess.Popen[bytes]":
        return subprocess.Popen(
            self._command(*args), cwd=self.root, env=self._env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )

    def out(self, *args: str) -> str | None:
        done = self.run(*args)
        return done.stdout.strip() if done.returncode == 0 else None


def materialize_tree(git: Any, commit: str, destination: Path) -> None:
    """Write the exact tree of ``commit`` to ``destination``: no ``.git``, no filters.

    ``git`` is any object with ``run(*args, text=False)`` and ``popen(*args)`` (this module's
    :class:`ObjectReader`, or the integrator's hardened ``_Git``). Blobs are copied byte for byte from
    ``git cat-file``; attributes (``export-ignore``, ``eol``, ``filter``) are never consulted, so a check
    sees what was reviewed and merged. Submodule links are skipped. Safety:

    * every path is validated first (no ``""``/``.``/``..``/``.git`` component, case-insensitively), and the
      whole listing is checked for duplicate entries and for a path that is both a file (or symlink) and a
      directory, before anything is written;
    * directories are created one component at a time relative to an open directory descriptor, and every
      component is opened with ``O_NOFOLLOW``, so no ancestor can be a symlink and nothing is ever created
      outside ``destination``;
    * any ``OSError`` is a :class:`CandidateTreeUnsafe`.
    """
    listing = git.run("ls-tree", "-r", "-z", "--full-tree", commit, text=False)
    if listing.returncode != 0:
        raise CandidateTreeUnreadable("cannot list the tree", reason_code="plan_unreadable")
    entries: list[tuple[str, str, str]] = []
    kinds: dict[str, str] = {}
    for raw in listing.stdout.split(b"\0"):
        if not raw:
            continue
        meta, _, path_bytes = raw.partition(b"\t")
        mode, kind, oid = meta.decode().split()
        path = os.fsdecode(path_bytes)
        parts = path.split("/")
        if any(part in ("", ".", "..") or part.lower() == ".git" for part in parts):
            raise CandidateTreeUnsafe(path, "forbidden path component")
        if kind != "blob":
            continue
        for depth in range(1, len(parts)):
            prefix = "/".join(parts[:depth])
            if kinds.setdefault(prefix, "dir") != "dir":
                raise CandidateTreeUnsafe(path, "an ancestor is also a file or symlink entry")
        if path in kinds:
            raise CandidateTreeUnsafe(path, "duplicate or clashing entry")
        kinds[path] = "file"
        entries.append((mode, oid, path))
    try:
        destination.mkdir(parents=True, exist_ok=False)
        root_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise CandidateTreeUnsafe(str(destination), f"cannot create the check tree: {exc.strerror}") from exc
    process = git.popen("cat-file", "--batch")
    assert process.stdin is not None and process.stdout is not None

    def feed() -> None:
        try:
            for _mode, oid, _path in entries:
                process.stdin.write(f"{oid}\n".encode())  # type: ignore[union-attr]
        except OSError:
            pass  # the reader stopped early (rejection); cat-file exits on EPIPE
        finally:
            with contextlib.suppress(OSError):
                process.stdin.close()  # type: ignore[union-attr]

    def parent_fd(parts: list[str]) -> int:
        fd = os.dup(root_fd)
        try:
            for part in parts:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(part, 0o755, dir_fd=fd)
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            return fd
        except BaseException:
            os.close(fd)
            raise

    writer = threading.Thread(target=feed, daemon=True)
    writer.start()
    try:
        for mode, oid, path in entries:
            header = process.stdout.readline().split()
            if len(header) != 3 or header[1] != b"blob":
                raise CandidateTreeUnreadable("cannot read a blob", reason_code="plan_unreadable", oid=oid)
            content = process.stdout.read(int(header[2]))
            process.stdout.read(1)  # the newline cat-file appends
            *ancestors, name = path.split("/")
            try:
                directory = parent_fd(ancestors)
                try:
                    if mode == "120000":
                        os.symlink(os.fsdecode(content), name, dir_fd=directory)
                    else:
                        descriptor = os.open(
                            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o755 if mode == "100755" else 0o644, dir_fd=directory,
                        )
                        with os.fdopen(descriptor, "wb") as handle:
                            handle.write(content)
                finally:
                    os.close(directory)
            except OSError as exc:
                raise CandidateTreeUnsafe(path, f"cannot materialize: {exc.strerror}") from exc
    finally:
        os.close(root_fd)
        process.stdout.close()
        writer.join(timeout=5)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


# ------------------------------------------------------------ the runner ----


def _check_item(command: str, result: Mapping[str, Any]) -> dict[str, Any]:
    if not result["started"] and not result["timed_out"]:
        reason: str | None = "sandbox_error"
    elif result["timed_out"]:
        reason = "timed_out"
    elif result["output_limit_exceeded"]:
        reason = "output_limit_exceeded"
    elif result["exit_code"] != 0:
        reason = "exit_nonzero"
    else:
        reason = None
    return {
        "command": command,
        "gate": command,
        "result": "pass" if reason is None else "fail",
        "reason": reason,
        "exit_code": result["exit_code"],
        "timed_out": bool(result["timed_out"]),
        "stdout_hash": result["stdout_hash"],
        "stderr_hash": result["stderr_hash"],
        "stdout_bytes": result["stdout_bytes"],
        "stderr_bytes": result["stderr_bytes"],
        "duration_ms": result["duration_ms"],
        "isolation": ISOLATION_RUNC,
        "gate_isolation": ISOLATION_RUNC,
    }


def run_candidate_checks(
    repo_root: str | os.PathLike[str],
    commit: str,
    checks: Sequence[str],
    *,
    label: str,
    timeout: float = DEFAULT_CHECK_TIMEOUT_SECONDS,
    spec_fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run ``checks`` against ``commit`` in the check environment (module docstring).

    ``label`` names the calling stage (``"verify"`` / ``"integrate"``) in file names and the result; it
    does not change behaviour. ``spec_fields`` are contract-bound identity fields (``task_id``,
    ``tree_hash``, ...) echoed as ``spec``; when ``tree_hash`` is present the commit's tree must equal
    it before anything runs. Returns ``{"passed", "checks", "environment", "label", "commit", "spec"}``
    and, when nothing could be run, ``failure_reason`` (``check_environment_setup_failed``,
    ``candidate_tree_mismatch`` or ``no_deterministic_checks``). Raises
    :class:`CheckEnvironmentUnavailable` when the sandbox is unavailable and :class:`CandidateTreeError`
    when the tree cannot be materialised safely.
    """
    status = require_sandbox()
    commands = tuple(item for item in checks if isinstance(item, str) and item.strip())
    spec = dict(spec_fields or {})
    outcome: dict[str, Any] = {
        "passed": False, "checks": [], "environment": sandbox_environment(status, timeout),
        "label": label, "commit": commit, "spec": spec,
    }
    if not commands:
        outcome["failure_reason"] = "no_deterministic_checks"
        return outcome
    if not _COMMIT.match(commit):
        raise ValueError("commit must be a full 40-hex object name")
    reader = ObjectReader(repo_root)
    tree_hash = reader.out("rev-parse", "--verify", f"{commit}^{{tree}}")
    expected = spec.get("tree_hash")
    if tree_hash is None or (expected is not None and tree_hash != expected):
        outcome["failure_reason"] = "candidate_tree_mismatch"
        return outcome
    work = Path(tempfile.mkdtemp(prefix="agentctl-check-env-"))
    try:
        tree_dir, out_dir = work / "tree", work / "out"
        materialize_tree(reader, commit, tree_dir)
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8", "TZ": "UTC",
            "PYTHONDONTWRITEBYTECODE": "1", "AGENTCTL_CANDIDATE_COMMIT": commit,
        }
        snapshot = sandbox.run_in_sandbox(
            _SNAPSHOT_COMMAND, tree_dir=str(tree_dir), timeout=SNAPSHOT_TIMEOUT_SECONDS,
            out_dir=str(out_dir), env=env, label=f"{label}-snapshot",
        )
        if snapshot["exit_code"] != 0 or snapshot["timed_out"]:
            outcome["failure_reason"] = "check_environment_setup_failed"
            outcome["setup"] = {
                "exit_code": snapshot["exit_code"], "timed_out": snapshot["timed_out"],
                "stderr_hash": snapshot["stderr_hash"], "started": snapshot["started"],
            }
            return outcome
        items: list[dict[str, Any]] = []
        for index, command in enumerate(commands):
            try:
                result = sandbox.run_in_sandbox(
                    command, tree_dir=str(tree_dir), timeout=float(timeout), out_dir=str(out_dir),
                    env=env, label=f"{label}-{index}",
                )
            except sandbox.SandboxUnavailable:
                result = {
                    "started": False, "timed_out": False, "output_limit_exceeded": False, "exit_code": None,
                    "stdout_hash": "", "stderr_hash": "", "stdout_bytes": 0, "stderr_bytes": 0, "duration_ms": 0,
                }
            items.append(_check_item(command, result))
        outcome["checks"] = items
        outcome["passed"] = all(item["result"] == "pass" for item in items)
        return outcome
    finally:
        shutil.rmtree(work, ignore_errors=True)
