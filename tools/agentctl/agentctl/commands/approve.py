"""Interactive issuance of hash-bound human approval artifacts."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import sys
from pathlib import Path

from ..approval import ApprovalManager, ApprovalRejected, create_approval, doctor
from ..config import load as load_config
from ..store import EventStore

NAME = "approve"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentctl approve", add_help=False)
    parser.add_argument("action")
    parser.add_argument("--target-type", required=True)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--target-hash", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--scope")
    parser.add_argument("--out")
    return parser


def _output_path(root: Path, action: str, target_id: str) -> Path:
    if action == "phase_authorization":
        name = f"{target_id}-phase-approval.json"
    elif action == "scope_authorization":
        name = f"{target_id}-approval.json"
    else:
        name = f"{target_id}-{action.replace('_authorization', '')}-approval.json"
    return root / "agent-system/work/P0/approvals" / name


def run(argv: list[str]) -> int:
    if not sys.stdin.isatty():
        raise ApprovalRejected(
            "approval_non_tty", "human approval issuance requires an interactive TTY"
        )
    args = _parser().parse_args(argv)
    config = load_config()
    health = doctor()
    policy = json.loads(
        (config.repo_root / "agent-system/policies/approval-principals.json").read_text(
            encoding="utf-8"
        )
    )
    matching = [
        principal
        for principal in policy["principals"]
        if principal["seal_key_fingerprint"] == health["fingerprint"]
    ]
    if len(matching) != 1:
        raise ApprovalRejected(
            "approval_principal_unknown", "seal key does not identify exactly one principal"
        )
    principal = matching[0]
    # The provisioning plan creates a raw 32-byte secret.  At Level 1, fresh
    # key unlock means the human supplies those bytes as 64 hex characters via
    # no-echo TTY input; agentctl never accepts a flag, environment variable,
    # pipe, or committed value as a substitute.
    supplied = getpass.getpass("Seal-key unlock (64 hex characters): ")
    try:
        key = bytes.fromhex(supplied)
    except ValueError as exc:
        raise ApprovalRejected("approval_unlock_failed", "seal-key unlock failed") from exc
    if len(key) != 32 or hashlib.sha256(key).hexdigest()[:16] != health["fingerprint"]:
        raise ApprovalRejected("approval_unlock_failed", "seal-key unlock failed")
    if len(args.target_hash) != 64 or any(c not in "0123456789abcdef" for c in args.target_hash):
        raise ApprovalRejected("approval_wrong_target_hash", "target hash must be lowercase SHA-256")
    record = create_approval(
        key=key,
        principal=principal["principal_id"],
        machine_identity=principal["machine_identity"],
        action=args.action,
        target_type=args.target_type,
        target_id=args.target_id,
        target_hash=args.target_hash,
        scope=args.scope or args.reason,
        reason=args.reason,
    )
    destination = Path(args.out).expanduser().resolve() if args.out else _output_path(
        config.repo_root, args.action, args.target_id
    )
    try:
        destination.relative_to(config.repo_root)
    except ValueError as exc:
        raise ApprovalRejected(
            "approval_output_outside_repository", "approval artifact output must be in the repository"
        ) from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    ApprovalManager(EventStore(), key=key).register(
        record, path=destination.relative_to(config.repo_root).as_posix()
    )
    print(destination.relative_to(config.repo_root).as_posix())
    return 0
