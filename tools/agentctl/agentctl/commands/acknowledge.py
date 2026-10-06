"""``agentctl acknowledge <task-id> <context-hash>`` — accept a leased context."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict

from ..scheduler import Scheduler
from ..store import EventStore

NAME = "acknowledge"


def run(argv: list[str]) -> int:
    if len(argv) != 2 or any(value.startswith("-") for value in argv):
        print("usage: agentctl acknowledge <task-id> <context-hash>", file=sys.stderr)
        return 2
    lease = Scheduler(EventStore()).acknowledge(argv[0], argv[1])
    print(json.dumps(asdict(lease), sort_keys=True, separators=(",", ":")))
    return 0
