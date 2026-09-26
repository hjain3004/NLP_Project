"""``agentctl gate <gate-id>`` — re-execute a phase gate's evidence."""

from __future__ import annotations

import json
import sys

from ..config import load as load_config
from ..gate import GateEvaluator
from ..store import EventStore

NAME = "gate"


def run(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0].startswith("-"):
        print("usage: agentctl gate <gate-id>", file=sys.stderr)
        return 2
    config = load_config()
    gate_id = argv[0]
    source = config.repo_root / "agent-system/work/P0/gates" / f"{gate_id}-criteria.json"
    result = GateEvaluator(config.repo_root, store=EventStore()).evaluate(source)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["gate_result"] != "failed" else 1
