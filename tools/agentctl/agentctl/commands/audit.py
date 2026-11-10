"""``agentctl audit ingest <gate-id> <artifact>``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ..config import load as load_config
from ..gate import ingest_audit, register_audit_dispatch
from ..store import EventStore
from ..validate import validate_artifact

NAME = "audit"


def run(argv: list[str]) -> int:
    if argv and argv[0] == "dispatch":
        if len(argv) != 2:
            print("usage: agentctl audit dispatch <assignment>", file=sys.stderr)
            return 2
        config = load_config()
        path = Path(argv[1]).expanduser().resolve()
        assignment = json.loads(path.read_text(encoding="utf-8"))
        try:
            source = path.relative_to(config.repo_root).as_posix()
        except ValueError:
            source = str(path)
        result = register_audit_dispatch(EventStore(), assignment, source_path=source)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    if len(argv) != 3 or argv[0] != "ingest":
        print("usage: agentctl audit dispatch <assignment> | audit ingest <gate-id> <artifact>", file=sys.stderr)
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
