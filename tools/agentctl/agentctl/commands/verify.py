"""``agentctl verify <task-id>`` — run trusted deterministic verification."""

import json
import sys

from ..gate_runner import drop_sensitive_environment
from ..verify import verify_task

NAME = "verify"


def run(argv: list[str]) -> int:
    # Verification runs gates at this process's uid; it reads no credential, so keep none.
    drop_sensitive_environment()
    if len(argv) != 1:
        print("usage: agentctl verify <task-id>", file=sys.stderr)
        return 2
    print(json.dumps(verify_task(argv[0]), sort_keys=True, separators=(",", ":")))
    return 0
