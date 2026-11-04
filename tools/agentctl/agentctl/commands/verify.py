"""``agentctl verify <task-id>`` — run trusted deterministic verification."""

import json
import sys

from ..check_environment import CheckEnvironmentUnavailable
from ..gate_runner import drop_sensitive_environment
from ..verify import verify_task

NAME = "verify"


def run(argv: list[str]) -> int:
    # Credentials are not needed here and must not be kept in this process's environment.
    drop_sensitive_environment()
    if len(argv) != 1:
        print("usage: agentctl verify <task-id>", file=sys.stderr)
        return 2
    try:
        result = verify_task(argv[0])
    except CheckEnvironmentUnavailable as exc:
        # Fail closed and say why: no check ran, the task did not change state.
        details = {key: value for key, value in exc.context.items() if key != "reason_code"}
        print(json.dumps(
            {"outcome": "refused", "reason_code": exc.reason_code, "message": exc.message, "details": details},
            sort_keys=True, separators=(",", ":"), default=str,
        ))
        print(f"agentctl: verify: refused: {exc.reason_code}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0
