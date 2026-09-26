"""``agentctl integrate`` Level-1 stub."""

import sys

from ..integrate import integrate_task

NAME = "integrate"


def run(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: agentctl integrate <task-id>", file=sys.stderr)
        return 2
    integrate_task(argv[0])
    return 0
