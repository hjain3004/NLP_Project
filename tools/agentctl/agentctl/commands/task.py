"""``agentctl task repair|supersede|cancel|status-chain``: the operator lifecycle.

* ``task repair <task-id> --evidence <review-id|artifact-id|attempt:ID> --note TEXT [--operator ID]``
  authorizes ``repair_required -> ready`` against new evidence (never extends the attempt ceiling);
* ``task supersede <old-id> --by <new-id> --reason TEXT [--operator ID]`` walks the old task to
  ``canceled`` along existing transition rows and re-points its dependents at the replacement;
* ``task cancel <task-id> --reason TEXT [--operator ID]`` is the same chain with no replacement
  (dependents become ``blocked``);
* ``task status-chain <task-id>`` prints the task's transitions, repair authorizations, cancellation
  decision, the chain a cancel would take, and its dependents (read-only).

Exit status: 0 done (including idempotent replays); 2 usage; 3 refused, nothing (more) changed.
The operator identity string is recorded verbatim and is self-declared, not authenticated.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from ..lifecycle import (
    LifecycleRefused,
    authorize_repair,
    cancel_task,
    status_chain,
    supersede_task,
)
from ..store import EventStore

NAME = "task"
USAGE = (
    "usage: agentctl task repair <task-id> --evidence <review-id|artifact-id|attempt:ID> --note TEXT [--operator ID]\n"
    "       agentctl task supersede <old-task-id> --by <new-task-id> --reason TEXT [--operator ID]\n"
    "       agentctl task cancel <task-id> --reason TEXT [--operator ID]\n"
    "       agentctl task status-chain <task-id>"
)

_OPTIONS = {
    "repair": {"--evidence", "--note", "--operator"},
    "supersede": {"--by", "--reason", "--operator"},
    "cancel": {"--reason", "--operator"},
    "status-chain": set(),
}


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _parse(subcommand: str, argv: list[str]) -> tuple[str, dict[str, str]] | None:
    allowed = _OPTIONS[subcommand]
    task_id: str | None = None
    values: dict[str, str] = {}
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument in allowed and index + 1 < len(argv) and argument not in values:
            values[argument] = argv[index + 1]
            index += 2
        elif argument.startswith("-") or task_id is not None:
            return None
        else:
            task_id, index = argument, index + 1
    required = allowed - {"--operator"}
    if task_id is None or not required <= set(values):
        return None
    return task_id, values


def run(argv: list[str]) -> int:
    if not argv or argv[0] not in _OPTIONS:
        print(USAGE, file=sys.stderr)
        return 2
    subcommand = argv[0]
    parsed = _parse(subcommand, argv[1:])
    if parsed is None:
        print(USAGE, file=sys.stderr)
        return 2
    task_id, values = parsed
    store = EventStore()
    try:
        if subcommand == "repair":
            payload = authorize_repair(
                store, task_id, evidence=values["--evidence"], note=values["--note"],
                operator=values.get("--operator"),
            )
        elif subcommand == "supersede":
            payload = supersede_task(
                store, task_id, by=values["--by"], reason=values["--reason"], operator=values.get("--operator")
            )
        elif subcommand == "cancel":
            payload = cancel_task(store, task_id, reason=values["--reason"], operator=values.get("--operator"))
        else:
            payload = status_chain(store, task_id)
    except LifecycleRefused as refused:
        _print({"task_id": task_id, "outcome": "refused", "reason_code": refused.reason_code,
                "message": refused.message, **{k: v for k, v in refused.context.items() if k != "reason_code"}})
        print(f"agentctl task {subcommand}: refused ({refused.reason_code}): {refused.message}", file=sys.stderr)
        return 3
    _print(payload)
    return 0
