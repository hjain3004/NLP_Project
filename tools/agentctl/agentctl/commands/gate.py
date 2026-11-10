"""``agentctl gate <gate-id>`` — re-execute a phase gate's evidence."""

from __future__ import annotations

import argparse
import json
import sys

from ..config import load as load_config
from ..evidence import verify_dry_run_semantics
from ..errors import VerificationFailure
from ..gate import GateEvaluator
from ..store import EventStore

NAME = "gate"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentctl gate", add_help=False)
    parser.add_argument("gate_id")
    parser.add_argument("--replay-dry-run")
    parser.add_argument("--dry-run-only", action="store_true")
    return parser


def run(argv: list[str]) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit:
        return 2
    if args.dry_run_only and not args.replay_dry_run:
        print("agentctl gate: --dry-run-only requires --replay-dry-run", file=sys.stderr)
        return 2
    config = load_config()
    gate_id = args.gate_id
    replay_path = args.replay_dry_run
    if gate_id == "G0" and replay_path is None:
        replay_path = "agent-system/work/DRY-RUN/evidence/events.json"
    if gate_id == "G0" and replay_path is not None:
        expected = (
            config.repo_root / "agent-system/work/DRY-RUN/evidence/events.json"
        ).resolve()
        if (config.repo_root / replay_path).resolve() != expected:
            raise VerificationFailure(
                "G0 must replay its exact committed DRY-RUN evidence", path=replay_path
            )
    if replay_path:
        try:
            verify_dry_run_semantics(
                replay_path,
                repository_root=config.repo_root,
                require_committed=True,
            )
        except (OSError, ValueError) as exc:
            raise VerificationFailure(
                "dry-run event evidence does not verify",
                path=replay_path,
                detail=str(exc),
            ) from exc
    source = config.repo_root / "agent-system/work/P0/gates" / f"{gate_id}-criteria.json"
    dispatch_key = None
    if not args.dry_run_only:
        try:
            dispatch_key = config.seal_key_path.read_bytes()
        except OSError:
            dispatch_key = None
    result = GateEvaluator(
        config.repo_root,
        store=None if args.dry_run_only else EventStore(),
        dispatch_key=dispatch_key,
    ).evaluate(
        source,
        write_result=not args.dry_run_only,
        evidence_types=frozenset({"deterministic_check"}) if args.dry_run_only else None,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["gate_result"] != "failed" else 1
