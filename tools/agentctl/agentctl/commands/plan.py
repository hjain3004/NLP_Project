"""``agentctl plan validate <work-package>``."""

from __future__ import annotations

import json
import sys

from ..plan import PlanLoader

NAME = "plan"


def run(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "validate":
        print("usage: agentctl plan validate <work-package>", file=sys.stderr)
        return 2
    print(json.dumps(PlanLoader().validate(argv[1]), sort_keys=True, separators=(",", ":")))
    return 0
