"""Interactive issuance of hash-bound human approval artifacts."""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import hashlib
import json
import sys
from pathlib import Path

from ..approval import (
    ApprovalManager,
    ApprovalRejected,
    authentication_method_for_action,
    build_approval,
    doctor,
    seal_approval,
)
from ..config import load as load_config
from ..local_authentication import (
    LocalAuthenticationAdapter,
    MacOSLocalAuthentication,
    authenticate_exact_hash,
)
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
    parser.add_argument("--auth-method")
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


def _issue_record(
    *,
    key: bytes,
    principal: str,
    machine_identity: str,
    action: str,
    target_type: str,
    target_id: str,
    target_hash: str,
    scope: str,
    reason: str,
    policy: dict,
    requested_authentication_method: str | None = None,
    authentication_adapter: LocalAuthenticationAdapter | None = None,
) -> dict:
    """Authenticate and seal one policy-selected immutable approval binding."""
    method = authentication_method_for_action(action, policy)
    if (
        requested_authentication_method is not None
        and requested_authentication_method != method
    ):
        raise ApprovalRejected(
            "approval_weaker_method",
            "requested authentication method is not the policy-selected method",
            action=action,
            required=method,
            actual=requested_authentication_method,
        )
    provisional = build_approval(
        principal=principal,
        machine_identity=machine_identity,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_hash=target_hash,
        scope=scope,
        reason=reason,
        authentication_method=method,
    )
    binding = (
        provisional["approval_id"],
        provisional["nonce"],
        provisional["authorized_action"],
        provisional["target_hash"],
        provisional["reason"],
    )
    if method == "local_authentication":
        adapter = authentication_adapter or MacOSLocalAuthentication()
        authenticate_exact_hash(
            adapter,
            action=provisional["authorized_action"],
            target_hash=provisional["target_hash"],
            reason=provisional["reason"],
            approval_id=provisional["approval_id"],
            nonce=provisional["nonce"],
        )
        provisional["authenticated_at"] = (
            dt.datetime.now(dt.timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
        if binding != (
            provisional["approval_id"],
            provisional["nonce"],
            provisional["authorized_action"],
            provisional["target_hash"],
            provisional["reason"],
        ):
            raise ApprovalRejected(
                "approval_authentication_failed",
                "approval binding changed during authentication",
            )
        # The record is not an approval artifact until this post-authentication
        # MAC seal is produced.  No file or event-store mutation occurs before
        # this point.
    return seal_approval(provisional, key)


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
    record = _issue_record(
        key=key,
        principal=principal["principal_id"],
        machine_identity=principal["machine_identity"],
        action=args.action,
        target_type=args.target_type,
        target_id=args.target_id,
        target_hash=args.target_hash,
        scope=args.scope or args.reason,
        reason=args.reason,
        policy=policy,
        requested_authentication_method=args.auth_method,
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
