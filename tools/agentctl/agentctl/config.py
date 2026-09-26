"""Runtime configuration resolution.

Resolves three things and refuses to start on an unsafe one:

* the repository root (the Git top-level directory);
* the runtime directory ``agent-system/runtime/``, created on first use — it is
  git-ignored, so it does not survive a clone, and Task 20 verifies agentctl
  from a fresh clone;
* the seal-key path used by the approval subsystem (Task 17).

The seal key must live **outside** the repository and outside every registered
worktree. A seal key inside the tree would be committed — a secret in the
history — and a task worker could read or forge it. That check is a hard
refusal, not a warning.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .errors import ConfigError, PermissionViolation

#: Environment variable naming the seal-key file. Absent is allowed; the
#: approval subsystem is what fails when it actually needs the key.
SEAL_KEY_ENV = "AGENTCTL_SEAL_KEY"

#: Default seal-key location when the environment does not name one. Kept in the
#: operator's home, deliberately far from any repository tree.
DEFAULT_SEAL_KEY = Path.home() / ".cashbooks" / "agentctl-seal.key"

_RUNTIME_RELATIVE = Path("agent-system") / "runtime"


@dataclass(frozen=True)
class Config:
    """Resolved, validated configuration for one agentctl invocation."""

    repo_root: Path
    runtime_dir: Path
    seal_key_path: Path

    def ensure_runtime_dir(self) -> Path:
        """Create ``agent-system/runtime/`` if absent and return it."""
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        return self.runtime_dir


def _run_git(args: list[str], cwd: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip()


@lru_cache(maxsize=1)
def repo_root(start: str | None = None) -> Path:
    """Absolute path to the Git top-level directory containing ``start``."""
    origin = Path(start or os.getcwd()).resolve()
    top = _run_git(["rev-parse", "--show-toplevel"], origin if origin.is_dir() else origin.parent)
    if top:
        return Path(top).resolve()
    # Fall back to walking up for a .git entry, so tests and fresh clones without
    # git on PATH still resolve.
    for candidate in (origin, *origin.parents):
        if (candidate / ".git").exists():
            return candidate
    raise ConfigError("not inside a Git repository", start=str(origin))


def worktree_roots(root: Path) -> list[Path]:
    """Every worktree path Git knows about, including the main one."""
    listing = _run_git(["worktree", "list", "--porcelain"], root)
    roots: list[Path] = []
    if listing:
        for line in listing.splitlines():
            if line.startswith("worktree "):
                roots.append(Path(line[len("worktree ") :]).resolve())
    if root not in roots:
        roots.append(root)
    return roots


def _is_within(path: Path, ancestor: Path) -> bool:
    try:
        path.relative_to(ancestor)
    except ValueError:
        return False
    return True


def validate_seal_key_location(seal_key_path: Path, root: Path) -> None:
    """Raise if ``seal_key_path`` sits inside the repository or any worktree."""
    resolved = seal_key_path.expanduser().resolve()
    if _is_within(resolved, root):
        raise PermissionViolation(
            "seal key must not live inside the repository",
            seal_key_path=str(resolved),
            repo_root=str(root),
        )
    for worktree in worktree_roots(root):
        if _is_within(resolved, worktree):
            raise PermissionViolation(
                "seal key must not live inside a worktree",
                seal_key_path=str(resolved),
                worktree=str(worktree),
            )


def seal_key_path(root: Path | None = None) -> Path:
    """Resolved seal-key path, checked to be outside the tree.

    Does not require the file to exist — only that, wherever it is, it is not
    somewhere a commit or a task worker could reach.
    """
    base = root or repo_root()
    raw = os.environ.get(SEAL_KEY_ENV)
    candidate = Path(raw).expanduser() if raw else DEFAULT_SEAL_KEY
    validate_seal_key_location(candidate, base)
    return candidate.resolve()


def load(start: str | None = None) -> Config:
    """Resolve the full configuration for this invocation."""
    root = repo_root(start)
    return Config(
        repo_root=root,
        runtime_dir=(root / _RUNTIME_RELATIVE).resolve(),
        seal_key_path=seal_key_path(root),
    )
