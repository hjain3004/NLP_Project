"""Hash-bound, HMAC-sealed human approval records.

The seal prevents accidental or in-worktree forgery.  It is a symmetric MAC,
not a digital signature, and therefore does not protect against a local actor
who can read the external key file (ADR-0005).
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
import subprocess
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import load as load_config
from .config import validate_seal_key_location
from .errors import ConfigError, HumanGate
from .events import make_event
from .hashing import canonical_json, sha256_of_json
from .jsonschema_subset import validate as validate_schema
from .store import EventStore

__all__ = [
    "SYNTHETIC_FIXTURE_KEY",
    "ApprovalManager",
    "ApprovalRejected",
    "authentication_method_for_action",
    "build_approval",
    "create_approval",
    "doctor",
    "resolve_approval_target",
    "seal_approval",
    "verify_approval",
]


SYNTHETIC_FIXTURE_KEY = b"cashbooks-public-synthetic-approval-fixture-key-v1"
_SEAL_EXCLUDED = frozenset({"control_plane_seal", "consumed_at", "revoked_at"})
_LOCAL_AUTHENTICATION = "local_authentication"


@dataclass(frozen=True)
class ApprovalTarget:
    """One repository-owned immutable target bound to an approval."""

    target_type: str
    target_id: str
    path: Path
    payload: Mapping[str, Any]
    target_hash: str


# Governance proposals are intentionally resolved through an explicit fixed
# identifier-to-path table. The approval record cannot select a filename,
# directory, or alternate proposal.
_APPROVAL_TARGETS = {
    "governance-change-set": {
        # Keep the original mapping so an already-committed v1 application can
        # be replayed against its historical target without consulting an
        # arbitrary caller-supplied path.
        "P0-GOVERNANCE-CHANGESET-2026-10-07": (
            "agent-system/work/P0/proposals/governance-change-set.json",
            "governance-change-set-proposal",
            "proposal_id",
        ),
        "P0-GOVERNANCE-CHANGESET-2026-10-11-V2": (
            "agent-system/work/P0/proposals/governance-change-set-v2.json",
            "governance-change-set-proposal",
            "proposal_id",
        ),
    },
}


def resolve_approval_target(
    target_type: str,
    target_id: str,
    *,
    repository_root: str | os.PathLike[str] | None = None,
    validate_operations: bool = True,
) -> ApprovalTarget:
    """Resolve a supported approval target using an explicit trusted mapping.

    This resolver is deliberately narrower than artifact discovery: a target
    record never supplies the path used to find its own payload.  Proposal
    status, discriminator, identifier, declared operation hashes, and the
    canonical payload hash are all checked before returning the target.
    """
    target_specs = _APPROVAL_TARGETS.get(target_type)
    if target_specs is None:
        raise ApprovalRejected(
            "approval_unknown_target",
            "approval target type is not registered",
            target_type=target_type,
        )
    spec = target_specs.get(target_id)
    if spec is None:
        raise ApprovalRejected(
            "approval_wrong_target",
            "approval target identifier is not registered",
            target_type=target_type,
            target_id=target_id,
        )
    root = (Path(repository_root) if repository_root is not None else load_config().repo_root).resolve()
    relative_path, expected_artifact_type, id_field = spec
    path = (root / relative_path).resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ApprovalRejected(
            "approval_target_unreadable",
            "approval target mapping escapes the repository",
            target_type=target_type,
        ) from exc
    if not path.is_file():
        raise ApprovalRejected(
            "approval_target_unreadable",
            "approval target cannot be read",
            path=relative_path,
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApprovalRejected(
            "approval_target_unreadable",
            "approval target cannot be read",
            path=relative_path,
        ) from exc
    if not isinstance(payload, dict):
        raise ApprovalRejected(
            "approval_target_mismatch",
            "approval target payload is not an object",
            path=relative_path,
        )
    if payload.get("artifact_type") != expected_artifact_type:
        raise ApprovalRejected(
            "approval_target_mismatch",
            "approval target artifact type is not the registered type",
            expected=expected_artifact_type,
            actual=payload.get("artifact_type"),
        )
    if payload.get(id_field) != target_id:
        raise ApprovalRejected(
            "approval_wrong_target",
            "approval target identifier does not match the registered payload",
            expected=payload.get(id_field),
            actual=target_id,
        )
    if payload.get("status") != "proposed" or payload.get("authoritative") is not False:
        raise ApprovalRejected(
            "approval_target_stale",
            "governance proposal is no longer a proposed non-authoritative target",
            path=relative_path,
        )
    try:
        # Import lazily to keep the approval primitive independent of proposal
        # command wiring while still enforcing every declared before-hash.
        if validate_operations:
            from .proposals import validate_governance_change_set

            validate_governance_change_set(path, repository_root=root)
    except (OSError, ValueError) as exc:
        raise ApprovalRejected(
            "approval_target_stale",
            "governance proposal no longer matches its declared immutable operations",
            path=relative_path,
        ) from exc
    return ApprovalTarget(
        target_type=target_type,
        target_id=target_id,
        path=path,
        payload=payload,
        target_hash=sha256_of_json(payload),
    )


def authentication_method_for_action(
    action: str, policy: Mapping[str, Any]
) -> str:
    """Select one implemented method allowed by the action's policy tier.

    Strong tiers are deliberately selected only when the policy explicitly
    permits the LocalAuthentication method.  ``keychain_backed`` is not an
    implemented fallback at Level 1, and no caller-provided method can change
    this decision.
    """
    tier = policy.get("tiers", {}).get(action)
    if tier == "interactive_tty_with_key_unlock":
        return "interactive_tty_with_key_unlock"
    if isinstance(tier, list) and _LOCAL_AUTHENTICATION in tier:
        return _LOCAL_AUTHENTICATION
    raise ApprovalRejected(
        "approval_weaker_method",
        "the required authentication tier has no implemented method",
        action=action,
        required=tier,
    )


class ApprovalRejected(HumanGate):
    """One stable approval rejection reason."""

    def __init__(self, code: str, message: str, **context: object) -> None:
        super().__init__(message, **context)
        self.code = code


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _timestamp(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _parse_time(value: object, *, field: str) -> dt.datetime:
    if not isinstance(value, str):
        raise ApprovalRejected("approval_unsigned", "approval timestamp is absent", field=field)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApprovalRejected(
            "approval_unsigned", "approval timestamp is malformed", field=field
        ) from exc
    if parsed.tzinfo is None:
        raise ApprovalRejected("approval_unsigned", "approval timestamp lacks timezone", field=field)
    return parsed.astimezone(dt.timezone.utc)


def _seal_payload(approval: Mapping[str, Any]) -> bytes:
    payload = {key: value for key, value in approval.items() if key not in _SEAL_EXCLUDED}
    return canonical_json(payload).encode("utf-8")


def seal_approval(approval: Mapping[str, Any], key: bytes) -> dict[str, Any]:
    """Return a copy carrying its HMAC-SHA256 control-plane seal."""
    if not isinstance(key, bytes) or not key:
        raise ValueError("approval seal key must be nonempty bytes")
    result = copy.deepcopy(dict(approval))
    result["control_plane_seal"] = hmac.new(
        key, _seal_payload(result), hashlib.sha256
    ).hexdigest()
    return result


def _load_policy(repository_root: Path | None = None) -> dict[str, Any]:
    root = repository_root or load_config().repo_root
    return json.loads(
        (root / "agent-system/policies/approval-policy.json").read_text(encoding="utf-8")
    )


def _load_principal_policy(repository_root: Path | None = None) -> dict[str, Any]:
    root = repository_root or load_config().repo_root
    return json.loads(
        (root / "agent-system/policies/approval-principals.json").read_text(
            encoding="utf-8"
        )
    )


def _require_principal_method(
    principal: object,
    method: object,
    *,
    principal_policy: Mapping[str, Any] | None = None,
    repository_root: Path | None = None,
) -> None:
    """Require the principal allow-list and action tier to agree.

    The public synthetic fixture deliberately has no live principal and is
    checked with ``SYNTHETIC_FIXTURE_KEY`` only.  Every real approval must be
    attributable to exactly one configured principal that explicitly permits
    the selected method.
    """
    if not isinstance(principal, str) or not isinstance(method, str):
        raise ApprovalRejected(
            "approval_principal_unknown", "approval principal or method is malformed"
        )
    try:
        principals = (principal_policy or _load_principal_policy(repository_root)).get(
            "principals", []
        )
    except (OSError, json.JSONDecodeError, AttributeError) as exc:
        raise ApprovalRejected(
            "approval_principal_unknown", "approval principals policy cannot be loaded"
        ) from exc
    matching = [
        item
        for item in principals
        if isinstance(item, Mapping) and item.get("principal_id") == principal
    ]
    if len(matching) != 1:
        raise ApprovalRejected(
            "approval_principal_unknown",
            "approval principal is not uniquely configured",
            principal=principal,
        )
    methods = matching[0].get("authentication_methods")
    if not isinstance(methods, list) or method not in methods:
        raise ApprovalRejected(
            "approval_weaker_method",
            "principal is not permitted to use the selected authentication method",
            principal=principal,
            actual=method,
        )


def _validate_shape(
    approval: Mapping[str, Any], repository_root: Path | None = None
) -> None:
    root = repository_root or load_config().repo_root
    try:
        schema = json.loads(
            (root / "agent-system/schemas/human-approval.schema.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ApprovalRejected(
            "approval_unsigned", "human-approval schema cannot be loaded"
        ) from exc
    ok, errors = validate_schema(approval, schema)
    if not ok:
        pointer, keyword, detail = errors[0]
        raise ApprovalRejected(
            "approval_unsigned",
            "approval does not conform to human-approval.schema.json",
            json_pointer=pointer,
            keyword=keyword,
            detail=detail,
        )


def verify_approval(
    approval: Mapping[str, Any],
    key: bytes,
    *,
    expected_action: str | None = None,
    expected_target_type: str | None = None,
    expected_target_id: str | None = None,
    expected_target_hash: str | None = None,
    expected_scope: str | None = None,
    now: dt.datetime | None = None,
    policy: Mapping[str, Any] | None = None,
    principal_policy: Mapping[str, Any] | None = None,
    repository_root: Path | None = None,
) -> Mapping[str, Any]:
    """Verify policy, binding, seal, lifetime, revocation, and consumption."""
    _validate_shape(approval, repository_root)
    if approval.get("artifact_type") != "human-approval":
        raise ApprovalRejected("approval_unsigned", "artifact is not a human approval")
    if expected_action is not None and approval.get("authorized_action") != expected_action:
        raise ApprovalRejected(
            "approval_wrong_action",
            "approval authorizes another action",
            expected=expected_action,
            actual=approval.get("authorized_action"),
        )
    if expected_target_type is not None and approval.get("target_type") != expected_target_type:
        raise ApprovalRejected(
            "approval_wrong_target",
            "approval binds another target type",
            expected=expected_target_type,
            actual=approval.get("target_type"),
        )
    if expected_target_id is not None and approval.get("target_id") != expected_target_id:
        raise ApprovalRejected(
            "approval_wrong_target",
            "approval binds another target identifier",
            expected=expected_target_id,
            actual=approval.get("target_id"),
        )
    if expected_target_hash is not None and approval.get("target_hash") != expected_target_hash:
        raise ApprovalRejected(
            "approval_wrong_target_hash",
            "approval binds another target hash",
            expected=expected_target_hash,
            actual=approval.get("target_hash"),
        )
    if expected_scope is not None and approval.get("scope") != expected_scope:
        raise ApprovalRejected(
            "approval_wrong_scope", "approval scope does not match", expected=expected_scope
        )

    if approval.get("target_type") in _APPROVAL_TARGETS:
        target = resolve_approval_target(
            str(approval["target_type"]),
            str(approval["target_id"]),
            repository_root=repository_root,
        )
        if approval.get("target_hash") != target.target_hash:
            raise ApprovalRejected(
                "approval_wrong_target_hash",
                "approval target hash does not match the current registered payload",
                expected=target.target_hash,
                actual=approval.get("target_hash"),
            )

    selected_policy = dict(policy or _load_policy())
    action = approval.get("authorized_action")
    tier = selected_policy.get("tiers", {}).get(action)
    method = approval.get("authentication_method")
    try:
        required_method = authentication_method_for_action(str(action), selected_policy)
    except ApprovalRejected:
        raise
    if method != required_method:
        raise ApprovalRejected(
            "approval_weaker_method",
            "approval used a weaker authentication method",
            action=action,
            required=required_method,
            actual=method,
        )
    if principal_policy is not None:
        _require_principal_method(
            approval.get("approver_principal"),
            method,
            principal_policy=principal_policy,
            repository_root=repository_root,
        )
    if key != SYNTHETIC_FIXTURE_KEY:
        _require_principal_method(
            approval.get("approver_principal"),
            method,
            repository_root=repository_root,
        )

    supplied = approval.get("control_plane_seal")
    if not isinstance(supplied, str) or len(supplied) != 64:
        raise ApprovalRejected("approval_unsigned", "approval has no valid seal field")
    expected_seal = hmac.new(key, _seal_payload(approval), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected_seal):
        raise ApprovalRejected("approval_unsigned", "approval seal does not verify")

    if approval.get("single_use") is not True:
        raise ApprovalRejected(
            "approval_not_single_use", "reusable human approvals are forbidden"
        )

    current = (now or _utc_now()).astimezone(dt.timezone.utc)
    if _parse_time(approval.get("expires_at"), field="expires_at") <= current:
        raise ApprovalRejected("approval_expired", "approval has expired")
    if approval.get("revoked_at") is not None:
        raise ApprovalRejected("approval_revoked", "approval has been revoked")
    if approval.get("consumed_at") is not None:
        raise ApprovalRejected("approval_consumed", "single-use approval was already consumed")
    return approval


class ApprovalManager:
    """Persist and atomically consume approval records through EventStore."""

    def __init__(
        self,
        store: EventStore,
        *,
        key: bytes | None = None,
        key_path: str | os.PathLike[str] | None = None,
        policy: Mapping[str, Any] | None = None,
        principal_policy: Mapping[str, Any] | None = None,
    ) -> None:
        self.store = store
        if key is None:
            path = Path(key_path).expanduser().resolve() if key_path else load_config().seal_key_path
            key = path.read_bytes()
        self.key = key
        self.policy = dict(policy or _load_policy(store.repository_root))
        self.principal_policy = (
            dict(principal_policy)
            if principal_policy is not None
            else None
        )

    def register(self, approval: Mapping[str, Any], *, path: str = "") -> dict[str, Any]:
        verify_approval(
            approval,
            self.key,
            policy=self.policy,
            principal_policy=self.principal_policy,
            repository_root=self.store.repository_root,
        )
        approval_id = str(approval["approval_id"])
        payload = json.dumps(dict(approval), sort_keys=True, separators=(",", ":"))
        event = make_event(
            event_type="human_approval_registered",
            reason_code="approval_registered",
            actor_role="human_owner",
            artifact_refs=[approval_id],
        )

        def mutation(connection: sqlite3.Connection) -> dict[str, Any]:
            connection.execute(
                "INSERT INTO artifacts(artifact_id, artifact_type, content_hash, path, "
                "integrable, payload_json) VALUES (?, 'human-approval', ?, ?, 1, ?)",
                (approval_id, sha256_of_json(approval), path, payload),
            )
            return {"approval_id": approval_id, "state": "registered"}

        return dict(
            self.store.execute(f"approval-register:{approval_id}", event, mutation)
        )

    def verify_registered(
        self, request: Mapping[str, str], *, now: dt.datetime | None = None
    ) -> Mapping[str, Any]:
        approval_id = str(request["approval_id"])
        rows = self.store.rows(
            "SELECT payload_json FROM artifacts WHERE artifact_id = ? "
            "AND artifact_type = 'human-approval'",
            (approval_id,),
        )
        if not rows:
            raise ApprovalRejected("approval_unsigned", "approval is not registered")
        record = json.loads(rows[0]["payload_json"])
        return verify_approval(
            record,
            self.key,
            expected_action=request["expected_action"],
            expected_target_type=request.get("expected_target_type") or None,
            expected_target_id=request.get("expected_target_id") or None,
            expected_target_hash=request["expected_target_hash"],
            expected_scope=request["expected_scope"],
            now=now,
            policy=self.policy,
            principal_policy=self.principal_policy,
            repository_root=self.store.repository_root,
        )

    def consume_in_transaction(
        self,
        connection: sqlite3.Connection,
        request: Mapping[str, str],
        *,
        now: dt.datetime | None = None,
    ) -> dict[str, Any]:
        """Verify and consume one approval inside an existing writer transaction."""
        approval_id = str(request["approval_id"])
        row = connection.execute(
            "SELECT payload_json FROM artifacts WHERE artifact_id = ? "
            "AND artifact_type = 'human-approval'",
            (approval_id,),
        ).fetchone()
        if row is None:
            raise ApprovalRejected("approval_unsigned", "approval is not registered")
        record = json.loads(row["payload_json"])
        verify_approval(
            record,
            self.key,
            expected_action=request["expected_action"],
            expected_target_type=request.get("expected_target_type") or None,
            expected_target_id=request.get("expected_target_id") or None,
            expected_target_hash=request["expected_target_hash"],
            expected_scope=request["expected_scope"],
            now=now,
            policy=self.policy,
            repository_root=self.store.repository_root,
        )
        consumed_at = _timestamp(now or _utc_now())
        record["consumed_at"] = consumed_at
        connection.execute(
            "UPDATE artifacts SET content_hash = ?, payload_json = ? WHERE artifact_id = ?",
            (
                sha256_of_json(record),
                json.dumps(record, sort_keys=True, separators=(",", ":")),
                approval_id,
            ),
        )
        return {"approval_id": approval_id, "consumed_at": consumed_at}

    def consume(
        self,
        approval_id: str,
        *,
        expected_action: str,
        expected_target_type: str | None = None,
        expected_target_id: str | None = None,
        expected_target_hash: str,
        expected_scope: str,
        now: dt.datetime | None = None,
    ) -> dict[str, Any]:
        results = self.consume_many(
            (
                {
                    "approval_id": approval_id,
                    "expected_action": expected_action,
                    "expected_target_type": expected_target_type or "",
                    "expected_target_id": expected_target_id or "",
                    "expected_target_hash": expected_target_hash,
                    "expected_scope": expected_scope,
                },
            ),
            now=now,
        )
        return results[0]

    def consume_many(
        self,
        requests: tuple[Mapping[str, str], ...],
        *,
        now: dt.datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Verify and consume a set of approvals in one writer transaction."""
        if not requests:
            return []
        consumed_at = _timestamp(now or _utc_now())
        approval_ids = tuple(str(request["approval_id"]) for request in requests)
        if len(set(approval_ids)) != len(approval_ids):
            raise ApprovalRejected(
                "approval_consumed", "one approval cannot satisfy two authority requirements"
            )
        event = make_event(
            event_type="human_approval_consumed",
            reason_code="approval_consumed",
            actor_role="control_plane",
            artifact_refs=list(approval_ids),
        )

        def mutation(connection: sqlite3.Connection) -> list[dict[str, Any]]:
            verified: list[tuple[str, dict[str, Any]]] = []
            for request in requests:
                approval_id = str(request["approval_id"])
                row = connection.execute(
                    "SELECT payload_json FROM artifacts WHERE artifact_id = ? "
                    "AND artifact_type = 'human-approval'",
                    (approval_id,),
                ).fetchone()
                if row is None:
                    raise ApprovalRejected("approval_unsigned", "approval is not registered")
                record = json.loads(row["payload_json"])
                verify_approval(
                    record,
                    self.key,
                    expected_action=request["expected_action"],
                    expected_target_type=request.get("expected_target_type") or None,
                    expected_target_id=request.get("expected_target_id") or None,
                    expected_target_hash=request["expected_target_hash"],
                    expected_scope=request["expected_scope"],
                    now=now,
                    policy=self.policy,
                    principal_policy=self.principal_policy,
                    repository_root=self.store.repository_root,
                )
                verified.append((approval_id, record))
            results: list[dict[str, Any]] = []
            for approval_id, record in verified:
                record["consumed_at"] = consumed_at
                connection.execute(
                    "UPDATE artifacts SET content_hash = ?, payload_json = ? WHERE artifact_id = ?",
                    (
                        sha256_of_json(record),
                        json.dumps(record, sort_keys=True, separators=(",", ":")),
                        approval_id,
                    ),
                )
                results.append({"approval_id": approval_id, "consumed_at": consumed_at})
            return results

        # A fresh operation key is intentional: a second caller must enter the
        # transaction and observe consumption, not replay the first result.
        return list(
            self.store.execute(
                f"approval-consume:{','.join(approval_ids)}:{uuid.uuid4()}", event, mutation
            )
        )


def build_approval(
    *,
    principal: str,
    machine_identity: str,
    action: str,
    target_type: str,
    target_id: str,
    target_hash: str,
    scope: str,
    reason: str,
    authentication_method: str = "interactive_tty_with_key_unlock",
    lifetime_seconds: int = 3600,
    now: dt.datetime | None = None,
    approval_id: str | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    """Build an unsealed approval for synchronous authentication binding.

    The returned record is intentionally not an approval artifact until
    ``seal_approval`` is called after the required authentication signal.
    ``approval_id`` and ``nonce`` are generated here so they are the exact
    values presented to the platform helper and then MAC-sealed.
    """
    issued = (now or _utc_now()).astimezone(dt.timezone.utc)
    record = {
        "artifact_type": "human-approval",
        "approval_id": approval_id or str(uuid.uuid4()),
        "schema_version": 1,
        "approver_principal": principal,
        "machine_identity": machine_identity,
        "authentication_method": authentication_method,
        "authenticated_at": _timestamp(issued),
        "authorized_action": action,
        "target_type": target_type,
        "target_id": target_id,
        "target_hash": target_hash,
        "scope": scope,
        "conditions": [],
        "reason": reason,
        "issued_at": _timestamp(issued),
        "expires_at": _timestamp(issued + dt.timedelta(seconds=lifetime_seconds)),
        "single_use": True,
        "consumed_at": None,
        "revoked_at": None,
        "revocation_reason": None,
        "nonce": nonce or secrets.token_hex(16),
        "control_plane_seal": "",
    }
    return record


def create_approval(
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
    lifetime_seconds: int = 3600,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Create a sealed approval for existing synthetic/library callers.

    Production issuance uses :func:`build_approval`, performs its required
    authentication, and seals only afterward.
    """
    record = build_approval(
        principal=principal,
        machine_identity=machine_identity,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_hash=target_hash,
        scope=scope,
        reason=reason,
        lifetime_seconds=lifetime_seconds,
        now=now,
    )
    return seal_approval(record, key)


def _first_approval_commit_time(root: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                "--diff-filter=A",
                "--reverse",
                "--format=%ct",
                "--",
                "tools/agentctl/agentctl/approval.py",
            ],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return float(lines[0]) if lines else None


def doctor(
    *,
    repository_root: str | os.PathLike[str] | None = None,
    key_path: str | os.PathLike[str] | None = None,
    principals_path: str | os.PathLike[str] | None = None,
    first_approval_commit_time: float | None = None,
) -> dict[str, Any]:
    """Validate key placement, mode, length, provenance time, and fingerprint."""
    config = load_config()
    root = Path(repository_root).resolve() if repository_root else config.repo_root
    selected_key = (
        Path(key_path).expanduser().resolve() if key_path else config.seal_key_path
    )
    validate_seal_key_location(selected_key, root)
    if not selected_key.is_file():
        raise ConfigError(
            "seal key is missing", reason_code="seal_key_missing", path=str(selected_key)
        )
    mode = stat.S_IMODE(selected_key.stat().st_mode)
    if mode != 0o600:
        raise ConfigError(
            "seal key mode must be exactly 0600",
            reason_code="seal_key_mode",
            mode=oct(mode),
        )
    key = selected_key.read_bytes()
    if len(key) != 32:
        raise ConfigError(
            "seal key must contain exactly 32 bytes",
            reason_code="seal_key_length",
            bytes=len(key),
        )
    fingerprint = hashlib.sha256(key).hexdigest()[:16]
    policy_path = (
        Path(principals_path).resolve()
        if principals_path
        else root / "agent-system/policies/approval-principals.json"
    )
    try:
        principals = json.loads(policy_path.read_text(encoding="utf-8"))["principals"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ConfigError(
            "approval principals policy cannot be loaded",
            reason_code="principals_unreadable",
            path=str(policy_path),
        ) from exc
    if fingerprint not in {
        principal.get("seal_key_fingerprint")
        for principal in principals
        if isinstance(principal, dict)
    }:
        raise ConfigError(
            "seal key fingerprint is not recorded",
            reason_code="fingerprint_unrecorded",
            fingerprint=fingerprint,
        )
    commit_time = (
        first_approval_commit_time
        if first_approval_commit_time is not None
        else _first_approval_commit_time(root)
    )
    if commit_time is not None and selected_key.stat().st_mtime > commit_time:
        raise ConfigError(
            "seal key was provisioned after the approval subsystem was committed",
            reason_code="seal_key_too_new",
        )
    return {"status": "ok", "fingerprint": fingerprint, "mode": "0600"}
