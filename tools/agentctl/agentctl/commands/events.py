"""``agentctl events export --work-package <id> --out <path>``."""

from __future__ import annotations

import json
import sys

from ..evidence import export_work_package_events
from ..store import EventStore

NAME = "events"


def run(argv: list[str]) -> int:
    if len(argv) != 5 or argv[0] != "export" or argv[1] != "--work-package" or argv[3] != "--out":
        print("usage: agentctl events export --work-package <id> --out <path>", file=sys.stderr)
        return 2
    result = export_work_package_events(EventStore(), argv[2], argv[4])
    print(json.dumps({"events": result["event_count"], "chain_hash": result["chain_hash"]}, sort_keys=True, separators=(",", ":")))
    return 0
