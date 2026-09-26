"""``agentctl dispatch <task-id>`` — issue one fenced execution lease."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict

from ..scheduler import Scheduler
from ..store import EventStore

NAME = "dispatch"


def run(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0].startswith("-"):
        print("usage: agentctl dispatch <task-id>", file=sys.stderr)
        return 2
    lease = Scheduler(EventStore()).dispatch(argv[0], worker_identity="manual-operator")
    print(json.dumps(asdict(lease), sort_keys=True, separators=(",", ":")))
    return 0
