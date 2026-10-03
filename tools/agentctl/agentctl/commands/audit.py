"""``agentctl audit ingest <gate-id> <artifact>``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ..config import load as load_config
from ..gate import ingest_audit
from ..store import EventStore
from ..validate import validate_artifact

NAME = "audit"


def run(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] != "ingest":
        print("usage: agentctl audit ingest <gate-id> <artifact>", file=sys.stderr)
        return 2
    config = load_config()
    path = Path(argv[2]).expanduser().resolve()
    artifact = validate_artifact(path, repository_root=config.repo_root)
    log = config.repo_root / "agent-system/work/P0/CONSTRUCTION-LOG.md"
    try:
        source = path.relative_to(config.repo_root).as_posix()
    except ValueError:
        source = ""
    result = ingest_audit(
        argv[1],
        artifact,
        construction_log=log,
        store=EventStore(),
        source_path=source,
        repository_root=config.repo_root,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0
