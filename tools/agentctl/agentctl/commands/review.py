"""``agentctl review prepare|ingest`` independent-review commands."""

from __future__ import annotations

import json
import sys

from ..review import ReviewIdentity, ReviewManager
from ..store import EventStore
from ..validate import validate_artifact

NAME = "review"


def run(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "prepare":
        result = ReviewManager(EventStore()).prepare(argv[1])
    elif len(argv) == 7 and argv[0] == "assign":
        result = ReviewManager(EventStore()).assign(
            argv[1], ReviewIdentity(*argv[2:])
        )
    elif len(argv) == 3 and argv[0] == "ingest":
        verdict = validate_artifact(argv[2])
        result = ReviewManager(EventStore()).ingest(argv[1], verdict)
    else:
        print(
            "usage: agentctl review prepare <task-id> | "
            "review assign <task-id> <review-task-id> <review-attempt-id> "
            "<provider-run-id> <model-session-id> <actor-role> | "
            "review ingest <task-id> <verdict>",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0
