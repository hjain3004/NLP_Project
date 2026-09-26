"""``agentctl doctor`` — validate the Level-1 control-plane environment."""

from __future__ import annotations

import json
import sys

from ..approval import doctor

NAME = "doctor"


def run(argv: list[str]) -> int:
    if argv:
        print("usage: agentctl doctor", file=sys.stderr)
        return 2
    print(json.dumps(doctor(), sort_keys=True, separators=(",", ":")))
    return 0
