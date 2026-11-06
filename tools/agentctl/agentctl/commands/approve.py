"""Interactive issuance of hash-bound human approval artifacts."""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import hashlib
import json
import os
import re
import shlex
import sys
import uuid
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
    parser.add_argument("--approval-id")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--recover-output", action="store_true")
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
    approval_id: str | None = None,
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
        approval_id=approval_id,
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
    approval_id = None
    if args.approval_id is not None:
        try:
            approval_id = str(uuid.UUID(args.approval_id))
        except (ValueError, AttributeError) as exc:
            raise ApprovalRejected("approval_input_invalid", "approval_id must be a lowercase UUID") from exc
        if approval_id != args.approval_id:
            raise ApprovalRejected("approval_input_invalid", "approval_id must be a lowercase UUID")
    if args.recover_output and approval_id is None:
        raise ApprovalRejected("approval_input_invalid", "recovery requires approval_id")
    if args.recover_output and args.auth_method is not None:
        raise ApprovalRejected(
            "approval_input_invalid", "authentication method is not valid for recovery"
        )
    if args.recover_output and not args.out:
        raise ApprovalRejected("approval_input_invalid", "recovery requires an output path")
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
        if approval_id is None:
            _legacy_destination(root, args.out)
        else:
            _contained_destination(root, args.out)
    result = {"method": method, "scope": scope}
    if approval_id is not None:
        result["approval_id"] = approval_id
    return result


def _contained_destination(root: Path, value: str) -> Path:
    """Return a contained destination resolved the way run() consumes it.

    Comparing the destination lexically against ``root`` wrongly rejects an
    alias path (a symlinked ancestor directory, or macOS /var versus
    /private/var) whose parent genuinely resolves inside the repository.
    The final path component is never resolved here, so a destination whose
    final component is itself a symlink is still rejected below rather than
    silently followed.
    """
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    destination = candidate.absolute()
    try:
        resolved_parent = destination.parent.resolve(strict=False)
        resolved_parent.relative_to(root.resolve())
    except ValueError as exc:
        raise ApprovalRejected(
            "approval_output_outside_repository",
            "approval artifact output must be in the repository",
        ) from exc
    if destination.is_symlink():
        raise ApprovalRejected("approval_output_invalid", "approval output cannot be a symlink")
    return resolved_parent / destination.name


def _legacy_destination(root: Path, value: str) -> Path:
    """Resolve a no-ID output exactly as the legacy command did."""
    destination = Path(value).expanduser().resolve()
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise ApprovalRejected(
            "approval_output_outside_repository",
            "approval artifact output must be in the repository",
        ) from exc
    return destination


def _write_exclusive_json(destination: Path, record: dict) -> None:
    """Create one approval mirror without following or replacing an output path."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags, 0o600)
    created = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, indent=2) + "\n")
    except BaseException:
        # Only remove the file opened by this call.  An exclusive open proves
        # that it did not replace a pre-existing output; inode comparison keeps
        # ordinary cleanup from unlinking a subsequently substituted path.
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            current = destination.stat(follow_symlinks=False)
            if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                destination.unlink()
        except OSError:
            pass
        raise


def _recovery_command(
    args: argparse.Namespace, approval_id: str, scope: str, destination: Path
) -> str:
    """Render a safe, repeatable recovery command without the original reason."""
    command = [
        "agentctl", "approve", args.action, "--target-type", args.target_type,
        "--target-id", args.target_id, "--target-hash", args.target_hash,
        "--scope", scope, "--reason", "recovery", "--approval-id", approval_id,
        "--recover-output", "--out", str(destination),
    ]
    return shlex.join(command)


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
    if args.recover_output:
        destination = _contained_destination(config.repo_root, args.out)
        manager = ApprovalManager(EventStore())
        record = dict(manager.verify_registered({
            "approval_id": validated["approval_id"],
            "expected_action": args.action,
            "expected_target_type": args.target_type,
            "expected_target_id": args.target_id,
            "expected_target_hash": args.target_hash,
            "expected_scope": validated["scope"],
        }))
        if record.get("approval_id") != validated["approval_id"]:
            raise ApprovalRejected("approval_unsigned", "registered approval identifier does not match")
        _write_exclusive_json(destination, record)
        print(json.dumps({
            "status": "recovered_registered_approval",
            "approval_id": validated["approval_id"],
            "output": destination.relative_to(config.repo_root).as_posix(),
        }, sort_keys=True))
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
            approval_id=validated.get("approval_id"),
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
            approval_id=validated.get("approval_id"),
        )
        key_for_registration = key
    if validated.get("approval_id") is None:
        # No-ID invocation remains the legacy command: its parent creation,
        # overwrite behavior, and CWD-relative explicit outputs are preserved.
        destination = _legacy_destination(
            config.repo_root,
            args.out or str(_output_path(config.repo_root, args.action, args.target_id)),
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        ApprovalManager(EventStore(), key=key_for_registration).register(
            record, path=destination.relative_to(config.repo_root).as_posix()
        )
    else:
        destination = _contained_destination(
            config.repo_root,
            args.out or str(_output_path(config.repo_root, args.action, args.target_id)),
        )
        ApprovalManager(EventStore(), key=key_for_registration).register(
            record, path=destination.relative_to(config.repo_root).as_posix()
        )
        try:
            _write_exclusive_json(destination, record)
        except OSError:
            print(json.dumps({
                "status": "approval_issued_output_failed",
                "approval_id": record["approval_id"],
                "target_hash": record["target_hash"],
                "recovery_command": _recovery_command(
                    args, record["approval_id"], validated["scope"], destination
                ),
            }, sort_keys=True))
            return 1
    print(destination.relative_to(config.repo_root).as_posix())
    return 0
