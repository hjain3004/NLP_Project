"""``agentctl status [<task-id>] [--json]``."""

from __future__ import annotations

import json
import sys

from ..store import EventStore

NAME = "status"


def run(argv: list[str]) -> int:
    as_json = False
    positional: list[str] = []
    for argument in argv:
        if argument == "--json":
            as_json = True
        elif argument.startswith("-"):
            print(f"agentctl status: unknown option {argument!r}", file=sys.stderr)
            return 2
        else:
            positional.append(argument)
    if len(positional) > 1:
        print("usage: agentctl status [<task-id>] [--json]", file=sys.stderr)
        return 2

    snapshot = EventStore().status(positional[0] if positional else None)
    if as_json:
        print(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
    else:
        print(json.dumps(snapshot, sort_keys=True, indent=2))
    return 0
