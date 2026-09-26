"""``agentctl ready [--json]`` — report the deterministic ready set."""

from __future__ import annotations

import json
import sys

from ..scheduler import Scheduler
from ..store import EventStore

NAME = "ready"


def run(argv: list[str]) -> int:
    if any(argument != "--json" for argument in argv) or argv.count("--json") > 1:
        print("usage: agentctl ready [--json]", file=sys.stderr)
        return 2
    scheduler = Scheduler(EventStore())
    scheduler.leases.expire_dead()
    rows = scheduler.ready()
    snapshot = {
        "ready": [
            {key: row[key] for key in ("task_id", "capability_class", "risk", "locks")}
            for row in rows if row["ready"]
        ],
        "blocked": [
            {"task_id": row["task_id"], "reason_code": row["reason_code"]}
            for row in rows if row["state"] == "ready" and not row["ready"]
        ],
    }
    print(json.dumps(snapshot, sort_keys=True, separators=(",", ":")) if "--json" in argv else json.dumps(snapshot, sort_keys=True, indent=2))
    return 0
