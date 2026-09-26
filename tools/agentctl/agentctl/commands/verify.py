"""``agentctl verify`` Level-1 stub."""

import sys

from ..verify import verify_task

NAME = "verify"


def run(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: agentctl verify <task-id>", file=sys.stderr)
        return 2
    verify_task(argv[0])
    return 0
