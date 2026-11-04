"""``agentctl review prepare|assign|ingest|run`` independent-review commands.

``review run <task-id> [--timeout SECONDS] [--max-budget-usd USD] [--export PATH]``
launches a *fresh* reviewer process under a pre-registered fenced assignment and
ingests its verdict; the other subcommands are the manual protocol and are unchanged.
"""

from __future__ import annotations

import json
import signal
import sys
from typing import Any

from ..execute import RunFailed, RunRefused, parse_run_options, review_task
from ..review import ReviewIdentity, ReviewManager
from ..store import EventStore
from ..validate import validate_artifact

NAME = "review"
RUN_USAGE = "usage: agentctl review run <task-id> [--timeout SECONDS] [--max-budget-usd USD] [--export PATH]"


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _terminate(_signum: int, _frame: Any) -> None:
    raise KeyboardInterrupt


def _review_run(argv: list[str]) -> int:
    options = parse_run_options(argv, RUN_USAGE)
    if options is None:
        return 2
    previous = signal.signal(signal.SIGTERM, _terminate)
    try:
        payload = review_task(
            options.task_id,
            timeout_seconds=options.timeout_seconds,
            max_budget_usd=options.max_budget_usd,
            export_path=options.export_path,
        )
    except RunRefused as refused:
        _print({"task_id": options.task_id, "outcome": "refused", "status": refused.status,
                "message": refused.message, **refused.payload})
        print(f"agentctl review run: refused ({refused.status}): {refused.message}", file=sys.stderr)
        return 3
    except RunFailed as failed:
        _print({"outcome": "failed", **failed.payload})
        print(f"agentctl review run: failed ({failed.failure_class}): {failed.message}", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)
    _print({"outcome": "reviewed", **payload})
    return 0


def run(argv: list[str]) -> int:
    if argv and argv[0] == "run":
        return _review_run(argv[1:])
    if len(argv) == 2 and argv[0] == "prepare":
        result = ReviewManager(EventStore()).prepare(argv[1])
    elif len(argv) == 7 and argv[0] == "assign":
        result = ReviewManager(EventStore()).assign(
            argv[1], ReviewIdentity(*argv[2:])
        )
    elif len(argv) == 3 and argv[0] == "ingest":
        verdict = validate_artifact(argv[2])
        result = ReviewManager(EventStore()).ingest(argv[1], verdict)
    else:
        print(
            "usage: agentctl review prepare <task-id> | "
            "review assign <task-id> <review-task-id> <review-attempt-id> "
            "<provider-run-id> <model-session-id> <actor-role> | "
            "review ingest <task-id> <verdict> | "
            "review run <task-id> [--timeout SECONDS] [--max-budget-usd USD] [--export PATH]",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0
