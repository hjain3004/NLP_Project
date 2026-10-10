"""Interactive issuance of hash-bound human approval artifacts."""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import hashlib
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path

from ..approval import (
    ApprovalManager,
    ApprovalRejected,
    authentication_method_for_action,
    build_approval,
    doctor,
    resolve_approval_target,
    seal_approval,
    _require_principal_method,
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
    parser.add_argument("--preflight", action="store_true")
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
    key: bytes | None,
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
    seal_key_loader: Callable[[], bytes] | None = None,
    principal_policy: dict | None = None,
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
    if key is None and seal_key_loader is None:
        raise ApprovalRejected(
            "approval_unlock_failed", "seal key is unavailable after authentication"
        )
    if principal_policy is not None:
        _require_principal_method(
            principal, method, principal_policy=principal_policy
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
        provisional["target_type"],
        provisional["target_id"],
        provisional["target_hash"],
        provisional["scope"],
        provisional["reason"],
    )
    if method == "local_authentication":
        adapter = authentication_adapter or MacOSLocalAuthentication()
        # Do not place the caller's free-form reason in a biometric prompt.
        # The reason digest binds the exact MAC-covered field without exposing
        # document text or PII; the target identity/hash/scope remain explicit.
        authentication_reason = (
            "Authorize this exact approval; reason SHA-256: "
            + hashlib.sha256(provisional["reason"].encode("utf-8")).hexdigest()
        )
        authenticate_exact_hash(
            adapter,
            action=provisional["authorized_action"],
            target_hash=provisional["target_hash"],
            reason=authentication_reason,
            target_type=provisional["target_type"],
            target_id=provisional["target_id"],
            scope=provisional["scope"],
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
            provisional["target_type"],
            provisional["target_id"],
            provisional["target_hash"],
            provisional["scope"],
            provisional["reason"],
        ):
            raise ApprovalRejected(
                "approval_authentication_failed",
                "approval binding changed during authentication",
            )
        # The record is not an approval artifact until this post-authentication
        # MAC seal is produced.  No file or event-store mutation occurs before
        # this point.
    selected_key = key if key is not None else seal_key_loader()
    return seal_approval(provisional, selected_key)


_TARGET_HASH = re.compile(r"^[0-9a-f]{64}$")


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApprovalRejected(
            "approval_target_unreadable", "approval target cannot be read"
        ) from exc
    if not isinstance(value, dict):
        raise ApprovalRejected("approval_target_unreadable", "approval target is not an object")
    return value


def _validate_inputs(args: argparse.Namespace, root: Path, policy: dict) -> dict[str, str]:
    """Validate all non-secret issuance inputs without prompts or writes."""
    for field in ("action", "target_type", "target_id", "reason"):
        value = getattr(args, field)
        if not isinstance(value, str) or not value.strip() or any(
            character in value for character in "\r\n\x00"
        ):
            raise ApprovalRejected("approval_input_invalid", f"{field} is malformed")
    if not _TARGET_HASH.fullmatch(args.target_hash):
        raise ApprovalRejected(
            "approval_wrong_target_hash", "target hash must be lowercase SHA-256"
        )
    scope = args.scope or args.reason
    if not scope.strip() or any(character in scope for character in "\r\n\x00"):
        raise ApprovalRejected("approval_input_invalid", "scope is malformed")
    method = authentication_method_for_action(args.action, policy)
    if args.auth_method is not None and args.auth_method != method:
        raise ApprovalRejected(
            "approval_weaker_method",
            "requested authentication method is not the policy-selected method",
            action=args.action,
            required=method,
            actual=args.auth_method,
        )
    if args.target_type == "governance-change-set":
        target = resolve_approval_target(
            args.target_type, args.target_id, repository_root=root
        )
        if target.target_hash != args.target_hash:
            raise ApprovalRejected(
                "approval_wrong_target_hash", "governance proposal hash is stale"
            )
    if args.out:
        destination = Path(args.out).expanduser().resolve()
        try:
            destination.relative_to(root)
        except ValueError as exc:
            raise ApprovalRejected(
                "approval_output_outside_repository",
                "approval artifact output must be in the repository",
            ) from exc
    return {"method": method, "scope": scope}


def _load_local_auth_seal_key(config, principal: dict) -> bytes:
    """Load the external seal key only after LocalAuthentication succeeds."""
    health = doctor()
    if health["fingerprint"] != principal.get("seal_key_fingerprint"):
        raise ApprovalRejected(
            "approval_principal_unknown", "seal key does not identify the authenticated principal"
        )
    try:
        return config.seal_key_path.read_bytes()
    except OSError as exc:
        raise ApprovalRejected(
            "approval_unlock_failed", "seal key cannot be loaded after authentication"
        ) from exc


def run(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    config = load_config()
    policy = _load_json(config.repo_root / "agent-system/policies/approval-policy.json")
    principal_policy = _load_json(
        config.repo_root / "agent-system/policies/approval-principals.json"
    )
    validated = _validate_inputs(args, config.repo_root, policy)
    if args.preflight:
        print(json.dumps({"status": "ok", **validated}, sort_keys=True))
        return 0
    if not sys.stdin.isatty():
        raise ApprovalRejected(
            "approval_non_tty", "human approval issuance requires an interactive TTY"
        )
    method = validated["method"]
    if method == "local_authentication":
        matching = [
            principal
            for principal in principal_policy["principals"]
            if method in principal.get("authentication_methods", [])
        ]
        if len(matching) != 1:
            raise ApprovalRejected(
                "approval_principal_unknown",
                "local authentication does not identify exactly one permitted principal",
            )
        principal = matching[0]
        loaded_key: list[bytes] = []

        def load_key_after_authentication() -> bytes:
            selected = _load_local_auth_seal_key(config, principal)
            loaded_key.append(selected)
            return selected

        record = _issue_record(
            key=None,
            seal_key_loader=load_key_after_authentication,
            principal=principal["principal_id"],
            machine_identity=principal["machine_identity"],
            action=args.action,
            target_type=args.target_type,
            target_id=args.target_id,
            target_hash=args.target_hash,
            scope=validated["scope"],
            reason=args.reason,
            policy=policy,
            requested_authentication_method=args.auth_method,
            principal_policy=principal_policy,
        )
        key_for_registration = loaded_key[0]
    else:
        health = doctor()
        matching = [
            principal
            for principal in principal_policy["principals"]
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
        record = _issue_record(
            key=key,
            principal=principal["principal_id"],
            machine_identity=principal["machine_identity"],
            action=args.action,
            target_type=args.target_type,
            target_id=args.target_id,
            target_hash=args.target_hash,
            scope=validated["scope"],
            reason=args.reason,
            policy=policy,
            requested_authentication_method=args.auth_method,
            principal_policy=principal_policy,
        )
        key_for_registration = key
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
    ApprovalManager(EventStore(), key=key_for_registration).register(
        record, path=destination.relative_to(config.repo_root).as_posix()
    )
    print(destination.relative_to(config.repo_root).as_posix())
    return 0
