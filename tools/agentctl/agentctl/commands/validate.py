"""``agentctl validate <path>`` — statically validate one artifact."""

from __future__ import annotations

import sys

from ..validate import validate_artifact

NAME = "validate"


def run(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0].startswith("-"):
        print("usage: agentctl validate <path>", file=sys.stderr)
        return 2
    validate_artifact(argv[0])
    print(f"valid: {argv[0]}")
    return 0
