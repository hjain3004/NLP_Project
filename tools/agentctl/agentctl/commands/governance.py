"""Apply the one approved governance change-set proposal."""

from __future__ import annotations

import argparse
import json
import sys

from ..governance import GovernanceApplier
from ..store import EventStore

NAME = "governance"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentctl governance", add_help=False)
    subparsers = parser.add_subparsers(dest="operation")
    apply = subparsers.add_parser("apply", add_help=False)
    apply.add_argument("--proposal", required=True)
    apply.add_argument("--approval", required=True)
    apply.add_argument("--preflight", action="store_true")
    return parser


def run(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    if args.operation != "apply":
        print("usage: agentctl governance apply --proposal <path> --approval <path> [--preflight]", file=sys.stderr)
        return 2
    applier = GovernanceApplier(EventStore())
    result = (
        applier.preflight(args.proposal, args.approval)
        if args.preflight
        else applier.apply(args.proposal, args.approval)
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0
