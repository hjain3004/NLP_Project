"""``agentctl context rebuild <task-id>``."""

from __future__ import annotations

import json
import sys

from ..context import ContextCompiler
from ..store import EventStore

NAME = "context"


def run(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "rebuild" or argv[1].startswith("-"):
        print("usage: agentctl context rebuild <task-id>", file=sys.stderr)
        return 2
    store = EventStore()
    manifest = ContextCompiler(store.repository_root, store).rebuild(argv[1])
    print(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    return 0
