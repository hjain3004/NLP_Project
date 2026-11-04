"""``agentctl run <task-id> [--timeout SECONDS] [--max-budget-usd USD] [--export PATH] [--allow-candidate-bindings]``.

Takes one *ready* task through routing, lease, a leased git worktree, one tool-less
provider invocation, a scope audit, a control-plane commit, and manifest ingestion.
Prints one compact JSON object. Exit status: 0 candidate ingested; 1 the run started and
failed (the attempt was ended without a success transition); 3 refused before any lease
was issued. Never retries. ``--allow-candidate-bindings`` (or ``AGENTCTL_ALLOW_CANDIDATE_BINDINGS=1``)
is the operator's explicit opt-in to a disclosed, unevaluated candidate model binding when the champion has no
runnable adapter; without it such a task is refused.
"""

from __future__ import annotations

import json
import signal
import sys
from typing import Any

from ..execute import RunFailed, RunRefused, parse_run_options, run_task

NAME = "run"
USAGE = (
    "usage: agentctl run <task-id> [--timeout SECONDS] [--max-budget-usd USD] [--export PATH] "
    "[--allow-candidate-bindings]"
)


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _terminate(_signum: int, _frame: Any) -> None:
    raise KeyboardInterrupt  # unwinds through run_task's cleanup (kills the provider group)


def run(argv: list[str]) -> int:
    options = parse_run_options(argv, USAGE)
    if options is None:
        return 2
    previous = signal.signal(signal.SIGTERM, _terminate)
    try:
        payload = run_task(
            options.task_id,
            timeout_seconds=options.timeout_seconds,
            max_budget_usd=options.max_budget_usd,
            export_path=options.export_path,
            allow_candidate_bindings=options.allow_candidate_bindings,
        )
    except RunRefused as refused:
        _print({"task_id": options.task_id, "outcome": "refused", "status": refused.status,
                "message": refused.message, **refused.payload})
        print(f"agentctl run: refused ({refused.status}): {refused.message}", file=sys.stderr)
        return 3
    except RunFailed as failed:
        _print({"outcome": "failed", **failed.payload})
        print(f"agentctl run: failed ({failed.failure_class}): {failed.message}", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)
    _print({"outcome": "candidate", **payload})
    return 0
