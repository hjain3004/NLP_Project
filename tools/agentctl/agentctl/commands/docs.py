"""``agentctl docs generate gates|verify``."""

from __future__ import annotations

import json
import sys

from ..config import repo_root
from ..docscheck import generate_phase_gates, verify_docs

NAME = "docs"


def run(argv: list[str]) -> int:
    root = repo_root()
    if argv == ["verify"]:
        print(json.dumps(verify_docs(root), sort_keys=True, separators=(",", ":")))
        return 0
    if argv == ["generate", "gates"]:
        destination = generate_phase_gates(root)
        print(destination.relative_to(root).as_posix())
        return 0
    print("usage: agentctl docs verify | docs generate gates", file=sys.stderr)
    return 2
