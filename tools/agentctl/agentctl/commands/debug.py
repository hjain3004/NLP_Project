"""Guarded, test-only control-plane fault injection."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from ..config import repo_root
from ..errors import PermissionViolation
from ..scheduler import Scheduler
from ..store import EventStore

NAME = "debug"
_ACTIVE = {"authorized", "active", "in_progress"}


def _load(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _assert_debug_allowed() -> None:
    work = repo_root() / "agent-system" / "work"
    for path in work.glob("**/*.json"):
        document = _load(path)
        if not document or document.get("artifact_type") != "work-package":
            continue
        try:
            relative = path.relative_to(work)
        except ValueError:  # pragma: no cover - glob root guarantees this
            continue
        if relative.parts and relative.parts[0] == "DRY-RUN":
            continue
        if document.get("bootstrap_phase") is True:
            continue
        if document.get("status") in _ACTIVE:
            raise PermissionViolation(
                "debug commands are disabled while a production work package is active",
                work_package_id=document.get("work_package_id"),
            )


def run(argv: list[str]) -> int:
    valid_expire = len(argv) == 2 and argv[0] == "expire-lease"
    valid_bump = len(argv) == 4 and argv[0] == "bump-interface"
    if not (valid_expire or valid_bump):
        print(
            "usage: agentctl debug expire-lease <task-id> | "
            "debug bump-interface <interface-id> <source-path> <version>",
            file=sys.stderr,
        )
        return 2
    _assert_debug_allowed()
    scheduler = Scheduler(EventStore())
    if valid_expire:
        result: object = {"task_id": argv[1], "expired": scheduler.expire_lease(argv[1], force=True)}
    else:
        result = {
            "interface_id": argv[1],
            "stale_tasks": list(scheduler.bump_interface(argv[1], argv[2], argv[3])),
        }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0
