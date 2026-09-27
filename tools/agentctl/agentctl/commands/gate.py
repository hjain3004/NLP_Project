"""``agentctl gate <gate-id>`` — re-execute a phase gate's evidence."""

from __future__ import annotations

import argparse
import json
import sys

from ..config import load as load_config
from ..evidence import verify_event_export
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
    if args.replay_dry_run:
        try:
            exported = verify_event_export(
                args.replay_dry_run, repository_root=config.repo_root
            )
        except (OSError, ValueError) as exc:
            raise VerificationFailure(
                "dry-run event evidence does not verify",
                path=args.replay_dry_run,
                detail=str(exc),
            ) from exc
        codes = {event.get("reason_code") for event in exported["events"]}
        required = {
            "multiple_ready_tasks",
            "path_lock_conflict",
            "semantic_lock_conflict",
            "stale_fencing_token",
            "self_review_rejected",
            "context_stale",
            "context_rebuilt",
        }
        missing = sorted(required - codes)
        if missing:
            raise VerificationFailure(
                "dry-run event evidence is incomplete", missing=missing
            )
    source = config.repo_root / "agent-system/work/P0/gates" / f"{gate_id}-criteria.json"
    result = GateEvaluator(
        config.repo_root,
        store=None if args.dry_run_only else EventStore(),
    ).evaluate(
        source,
        write_result=not args.dry_run_only,
        evidence_types=frozenset({"deterministic_check"}) if args.dry_run_only else None,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["gate_result"] != "failed" else 1
