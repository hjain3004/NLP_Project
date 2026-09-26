"""``agentctl ingest <task-id> <manifest>`` — ingest a fenced candidate."""

from __future__ import annotations

import json
import sys

from ..errors import InvalidArtifact
from ..scheduler import Scheduler
from ..store import EventStore
from ..validate import validate_artifact

NAME = "ingest"


def run(argv: list[str]) -> int:
    if len(argv) != 2 or any(argument.startswith("-") for argument in argv):
        print("usage: agentctl ingest <task-id> <manifest>", file=sys.stderr)
        return 2
    scheduler = Scheduler(EventStore())
    try:
        manifest = validate_artifact(argv[1])
    except InvalidArtifact:
        scheduler.handle_schema_error(argv[0], [])
        raise
    result = scheduler.ingest_manifest(argv[0], manifest)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0
