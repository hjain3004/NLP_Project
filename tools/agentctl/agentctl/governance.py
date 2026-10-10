"""Fail-closed application of the approved governance change-set proposal.

The proposal is data until this module verifies the fixed target, the complete
operation manifest, and a MAC-sealed approval.  Filesystem replacement is
recoverable and is performed inside the same serialized state mutation that
consumes the approval and appends the application event.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import stat
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .approval import ApprovalManager, resolve_approval_target, verify_approval
from .errors import InvalidArtifact
from .events import make_event
from .hashing import canonical_json, sha256_of_file, sha256_of_json
from .store import EventStore

__all__ = ["GovernanceApplier", "GovernanceApplyError", "GovernancePlan"]


class GovernanceApplyError(InvalidArtifact):
    """A proposal cannot be applied safely or recovery is incomplete."""

    code = "governance_apply_rejected"


_PROPOSAL_RELATIVE = Path("agent-system/work/P0/proposals/governance-change-set.json")
_APPROVAL_DIRECTORY = Path("agent-system/work/P0/approvals")
_JOURNAL_RELATIVE = Path("agent-system/runtime/governance-apply-journal.json")
_EXPECTED_SCOPE = "Apply only the exact governance-change-set proposal operations"
_EXPECTED_ACTION = "schema_commitment"
_EXPECTED_TYPE = "governance-change-set"
_EXPECTED_PATHS = frozenset(
    {
        "agent-system/schemas/task-contract.schema.json",
        "agent-system/schemas/implementation-manifest.schema.json",
        "agent-system/schemas/work-package.schema.json",
        "agent-system/schemas/human-approval.schema.json",
        "agent-system/policies/approval-policy.json",
        "agent-system/templates/task-contract.json",
        "agent-system/templates/implementation-manifest.json",
        "agent-system/templates/work-package.json",
        "agent-system/templates/human-approval.json",
        "tools/agentctl/agentctl/validate.py",
    }
)
_HEX64 = set("0123456789abcdef")


@dataclass(frozen=True)
class _Replacement:
    relative_path: str
    before_hash: str
    after_hash: str
    replacement: bytes


@dataclass(frozen=True)
class GovernancePlan:
    proposal_id: str
    proposal_hash: str
    approval_id: str
    approval_hash: str
    replacements: tuple[_Replacement, ...]

    @property
    def idempotency_key(self) -> str:
        return f"governance-apply:{self.proposal_id}:{self.approval_id}"


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GovernanceApplyError("JSON artifact cannot be read", path=str(path)) from exc
    if not isinstance(value, Mapping):
        raise GovernanceApplyError("JSON artifact must be an object", path=str(path))
    return value


def _assert_sha(value: object, *, field: str, path: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in _HEX64 for c in value):
        raise GovernanceApplyError("proposal hash field is malformed", field=field, path=path)
    return value


def _safe_path(root: Path, supplied: str | Path, *, required_relative: Path | None = None) -> Path:
    candidate = Path(supplied)
    lexical = (candidate if candidate.is_absolute() else root / candidate).absolute()
    # ``/var`` is a system alias for ``/private/var`` on macOS.  When an
    # absolute caller path uses that alias, resolve it for containment first;
    # otherwise inspect the caller's lexical components for symlinks.
    try:
        lexical_relative = lexical.relative_to(root)
    except ValueError:
        lexical_relative = None
    if lexical_relative is not None:
        for index, _part in enumerate(lexical_relative.parts):
            if (root / Path(*lexical_relative.parts[: index + 1])).is_symlink():
                raise GovernanceApplyError("symlink paths are forbidden", path=str(lexical_relative))
    resolved = lexical.resolve(strict=False)
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise GovernanceApplyError("path escapes repository", path=str(supplied)) from exc
    if required_relative is not None and relative != required_relative:
        raise GovernanceApplyError("path is not the registered fixed target", path=str(supplied))
    if not resolved.is_file() or resolved.is_symlink():
        raise GovernanceApplyError("artifact path is not a regular file", path=str(relative))
    return resolved


def _strict_patch(document: Any, patch: Mapping[str, Any], *, path: str) -> Any:
    pointer = patch.get("pointer")
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise GovernanceApplyError("proposal JSON pointer is malformed", path=path)
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
    if not parts or any(not part for part in parts):
        raise GovernanceApplyError("proposal JSON pointer is malformed", path=path)
    parent = document
    try:
        for part in parts[:-1]:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        leaf = parts[-1]
        operation = patch.get("op")
        if operation == "append" and isinstance(parent, dict) and isinstance(parent.get(leaf), list):
            parent[leaf].append(copy.deepcopy(patch["value"]))
        elif operation == "set" and isinstance(parent, dict):
            parent[leaf] = copy.deepcopy(patch["value"])
        else:
            raise GovernanceApplyError("proposal patch does not match its pointer", path=path)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise GovernanceApplyError("proposal patch pointer is not applicable", path=path) from exc
    return document


def _replacement(root: Path, operation: Mapping[str, Any]) -> _Replacement:
    relative = operation.get("path")
    if not isinstance(relative, str) or relative not in _EXPECTED_PATHS:
        raise GovernanceApplyError("proposal contains an unknown target path", path=str(relative))
    target = _safe_path(root, relative)
    before = target.read_bytes()
    before_hash = _assert_sha(operation.get("before_bytes_sha256"), field="before_bytes_sha256", path=relative)
    if _hash_bytes(before) != before_hash:
        raise GovernanceApplyError("canonical target changed before application", path=relative)
    encoding = operation.get("encoding")
    if encoding == "canonical_json_utf8":
        try:
            document = json.loads(before.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GovernanceApplyError("JSON target cannot be decoded", path=relative) from exc
        if not isinstance(document, dict):
            raise GovernanceApplyError("JSON target must be an object", path=relative)
        patches = operation.get("patches")
        if not isinstance(patches, list) or not patches:
            raise GovernanceApplyError("JSON proposal operation lacks patches", path=relative)
        for patch in patches:
            if not isinstance(patch, Mapping):
                raise GovernanceApplyError("JSON proposal patch is not an object", path=relative)
            _strict_patch(document, patch, path=relative)
        replacement = canonical_json(document).encode("utf-8")
        after_hash = _assert_sha(operation.get("after_canonical_json_sha256"), field="after_canonical_json_sha256", path=relative)
    elif encoding == "utf8_patch":
        replace = operation.get("replace")
        with_value = operation.get("with")
        if not isinstance(replace, str) or not isinstance(with_value, str):
            raise GovernanceApplyError("UTF-8 proposal patch is malformed", path=relative)
        old = replace.encode("utf-8")
        new = with_value.encode("utf-8")
        if before.count(old) != 1:
            raise GovernanceApplyError("UTF-8 proposal patch is not unique", path=relative)
        replacement = before.replace(old, new, 1)
        after_hash = _assert_sha(operation.get("after_bytes_sha256"), field="after_bytes_sha256", path=relative)
    else:
        raise GovernanceApplyError("proposal operation encoding is unknown", path=relative)
    if _hash_bytes(replacement) != after_hash:
        raise GovernanceApplyError("proposal replacement hash does not match", path=relative)
    return _Replacement(relative, before_hash, after_hash, replacement)


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class GovernanceApplier:
    """Prepare, verify, apply, and recover one exact governance proposal."""

    def __init__(
        self,
        store: EventStore,
        *,
        key: bytes | None = None,
        key_path: str | os.PathLike[str] | None = None,
        failure_injector: Callable[[str, int], None] | None = None,
    ) -> None:
        self.store = store
        self.root = store.repository_root.resolve()
        self.manager = ApprovalManager(store, key=key, key_path=key_path)
        self.failure_injector = failure_injector
        self.journal_path = self.root / _JOURNAL_RELATIVE

    def _load_plan(self, proposal_source: str | Path, approval_source: str | Path) -> GovernancePlan:
        proposal_path = _safe_path(self.root, proposal_source, required_relative=_PROPOSAL_RELATIVE)
        approval_path = _safe_path(self.root, approval_source)
        try:
            approval_path.relative_to(self.root / _APPROVAL_DIRECTORY)
        except ValueError as exc:
            raise GovernanceApplyError("approval path is outside the fixed approval directory") from exc
        proposal = _read_json(proposal_path)
        if proposal.get("artifact_type") != "governance-change-set-proposal" or proposal.get("status") != "proposed" or proposal.get("authoritative") is not False:
            raise GovernanceApplyError("proposal is not a proposed non-authoritative governance change set")
        operations = proposal.get("operations")
        if not isinstance(operations, list) or len(operations) != len(_EXPECTED_PATHS):
            raise GovernanceApplyError("governance proposal must contain exactly ten operations")
        operation_paths = [item.get("path") for item in operations if isinstance(item, Mapping)]
        if len(operation_paths) != len(set(operation_paths)) or set(operation_paths) != _EXPECTED_PATHS:
            raise GovernanceApplyError("governance proposal operation manifest is not exactly the approved ten paths")
        replacements = tuple(_replacement(self.root, operation) for operation in operations if isinstance(operation, Mapping))
        proposal_hash = sha256_of_json(proposal)
        approval = _read_json(approval_path)
        target = resolve_approval_target(_EXPECTED_TYPE, str(proposal.get("proposal_id")), repository_root=self.root)
        if target.target_hash != proposal_hash:
            raise GovernanceApplyError("proposal hash does not match the registered target")
        approval_id = approval.get("approval_id")
        if not isinstance(approval_id, str) or not approval_id:
            raise GovernanceApplyError("approval identity is malformed")
        verify_approval(
            approval,
            self.manager.key,
            expected_action=_EXPECTED_ACTION,
            expected_target_type=_EXPECTED_TYPE,
            expected_target_id=str(proposal["proposal_id"]),
            expected_target_hash=proposal_hash,
            expected_scope=_EXPECTED_SCOPE,
            policy=self.manager.policy,
            repository_root=self.root,
        )
        return GovernancePlan(
            proposal_id=str(proposal["proposal_id"]),
            proposal_hash=proposal_hash,
            approval_id=approval_id,
            approval_hash=sha256_of_json(approval),
            replacements=replacements,
        )

    def _journal(self, plan: GovernancePlan, state: str, entries: list[dict[str, str]]) -> None:
        payload = {
            "schema_version": 1,
            "state": state,
            "proposal_id": plan.proposal_id,
            "proposal_hash": plan.proposal_hash,
            "approval_id": plan.approval_id,
            "approval_hash": plan.approval_hash,
            "idempotency_key": plan.idempotency_key,
            "entries": entries,
        }
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.journal_path.with_name(f".{self.journal_path.name}.{secrets.token_hex(8)}.tmp")
        temporary.write_text(canonical_json(payload), encoding="utf-8")
        _fsync_file(temporary)
        os.replace(temporary, self.journal_path)
        _fsync_directory(self.journal_path.parent)

    def _read_journal(self) -> dict[str, Any] | None:
        if self.journal_path.is_symlink():
            raise GovernanceApplyError("recovery journal symlink is forbidden")
        if not self.journal_path.exists():
            return None
        journal = _read_json(self.journal_path)
        if journal.get("schema_version") != 1 or not isinstance(journal.get("entries"), list):
            raise GovernanceApplyError("recovery journal is malformed")
        for entry in journal["entries"]:
            if not isinstance(entry, Mapping) or not all(isinstance(entry.get(k), str) for k in ("path", "stage", "backup", "before_hash", "after_hash")):
                raise GovernanceApplyError("recovery journal entry is malformed")
            # During an interrupted rename the canonical target may be absent;
            # validate the relative path lexically here and inspect existence
            # only in the recovery state machine below.
            target = (self.root / entry["path"]).resolve(strict=False)
            try:
                target.relative_to(self.root)
            except ValueError as exc:
                raise GovernanceApplyError("recovery target escapes repository") from exc
            relative_target = target.relative_to(self.root)
            for index, _part in enumerate(relative_target.parts):
                current = self.root / Path(*relative_target.parts[: index + 1])
                if current.is_symlink():
                    raise GovernanceApplyError("recovery target is a symlink")
            for key in ("stage", "backup"):
                lexical = self.root / entry[key]
                if lexical.is_symlink() or not lexical.name.startswith(f".{target.name}.agentctl-"):
                    raise GovernanceApplyError("recovery journal artifact is unsafe")
                candidate = lexical.resolve(strict=False)
                try:
                    candidate.relative_to(self.root)
                except ValueError as exc:
                    raise GovernanceApplyError("recovery journal path escapes repository") from exc
                if candidate.parent != target.parent:
                    raise GovernanceApplyError("recovery journal artifact is not adjacent to target")
        return dict(journal)

    def recover(self) -> dict[str, Any]:
        """Recover an interrupted transaction without consuming approval."""
        journal = self._read_journal()
        if journal is None:
            return {"status": "clean"}
        key = str(journal.get("idempotency_key", ""))
        event = self.store.rows("SELECT result_json FROM events WHERE idempotency_key = ?", (key,))
        entries = journal["entries"]
        if event:
            result = json.loads(event[0]["result_json"])
            if result.get("proposal_hash") != journal.get("proposal_hash") or result.get("approval_id") != journal.get("approval_id"):
                raise GovernanceApplyError("recovery event does not match the journal")
            self._cleanup_entries(entries)
            self.journal_path.unlink(missing_ok=True)
            _fsync_directory(self.journal_path.parent)
            return {"status": "committed", "result": result}
        # No committed event means the database transaction rolled back.  Always
        # restore the before tree; this makes recovery independent of crash point.
        for entry in entries:
            target = self.root / entry["path"]
            backup = self.root / entry["backup"]
            if backup.exists():
                if backup.is_symlink() or not backup.is_file():
                    raise GovernanceApplyError("recovery backup is unsafe", path=str(entry["path"]))
                os.replace(backup, target)
                _fsync_directory(target.parent)
            elif not target.is_file() or sha256_of_file(target) != entry["before_hash"]:
                raise GovernanceApplyError("recovery cannot prove the before tree", path=str(entry["path"]))
        for entry in entries:
            target = self.root / entry["path"]
            if sha256_of_file(target) != entry["before_hash"]:
                raise GovernanceApplyError("recovery before-hash check failed", path=str(entry["path"]))
        self._cleanup_entries(entries)
        self.journal_path.unlink(missing_ok=True)
        _fsync_directory(self.journal_path.parent)
        return {"status": "rolled_back"}

    def _cleanup_entries(self, entries: list[Mapping[str, str]]) -> None:
        for entry in entries:
            for key in ("stage", "backup"):
                candidate = self.root / entry[key]
                if candidate.is_symlink():
                    raise GovernanceApplyError("recovery artifact is unsafe")
                if candidate.exists():
                    if not candidate.is_file():
                        raise GovernanceApplyError("recovery artifact is unsafe")
                    candidate.unlink()
                    _fsync_directory(candidate.parent)

    def preflight(self, proposal_source: str | Path, approval_source: str | Path) -> dict[str, Any]:
        recovery = self.recover()
        plan = self._load_plan(proposal_source, approval_source)
        expires = _read_json(_safe_path(self.root, approval_source)).get("expires_at")
        return {
            "status": "ok",
            "proposal_id": plan.proposal_id,
            "proposal_hash": plan.proposal_hash,
            "approval_id": plan.approval_id,
            "operation_count": len(plan.replacements),
            "recovery": recovery["status"],
            "expires_at": expires,
        }

    def _prepare_filesystem(self, plan: GovernancePlan) -> None:
        token = uuid.uuid4().hex
        entries: list[dict[str, str]] = []
        for replacement in plan.replacements:
            target = _safe_path(self.root, replacement.relative_path)
            if sha256_of_file(target) != replacement.before_hash:
                raise GovernanceApplyError("target changed before mutation", path=replacement.relative_path)
            stage = target.parent / f".{target.name}.agentctl-stage-{token}"
            backup = target.parent / f".{target.name}.agentctl-backup-{token}"
            entries.append({"path": replacement.relative_path, "stage": str(stage.relative_to(self.root)), "backup": str(backup.relative_to(self.root)), "before_hash": replacement.before_hash, "after_hash": replacement.after_hash})
        self._journal(plan, "preparing", entries)
        for replacement, entry in zip(plan.replacements, entries):
            target = self.root / entry["path"]
            stage = self.root / entry["stage"]
            mode = stat.S_IMODE(target.stat().st_mode)
            stage.write_bytes(replacement.replacement)
            os.chmod(stage, mode)
            _fsync_file(stage)
            _fsync_directory(target.parent)
        for entry in entries:
            target = self.root / entry["path"]
            if sha256_of_file(target) != entry["before_hash"]:
                raise GovernanceApplyError("target changed while staging", path=entry["path"])
        self._journal(plan, "prepared", entries)
        if self.failure_injector:
            self.failure_injector("after_staging", -1)
        for index, entry in enumerate(entries):
            target = self.root / entry["path"]
            stage = self.root / entry["stage"]
            backup = self.root / entry["backup"]
            os.replace(target, backup)
            _fsync_directory(target.parent)
            os.replace(stage, target)
            _fsync_file(target)
            _fsync_directory(target.parent)
            if sha256_of_file(target) != entry["after_hash"]:
                raise GovernanceApplyError("replacement after-hash check failed", path=entry["path"])
            if self.failure_injector:
                self.failure_injector("mid_rename", index)
        for entry in entries:
            target = self.root / entry["path"]
            if sha256_of_file(target) != entry["after_hash"]:
                raise GovernanceApplyError("after tree hash check failed", path=entry["path"])
        self._journal(plan, "applied", entries)

    def _mutation(self, plan: GovernancePlan, approval: Mapping[str, Any], connection: sqlite3.Connection) -> dict[str, Any]:
        payload = json.dumps(dict(approval), sort_keys=True, separators=(",", ":"))
        row = connection.execute("SELECT payload_json, content_hash FROM artifacts WHERE artifact_id = ? AND artifact_type = 'human-approval'", (plan.approval_id,)).fetchone()
        if row is None:
            connection.execute("INSERT INTO artifacts(artifact_id, artifact_type, content_hash, path, integrable, payload_json) VALUES (?, 'human-approval', ?, ?, 1, ?)", (plan.approval_id, plan.approval_hash, str(_APPROVAL_DIRECTORY / f"{plan.approval_id}.json"), payload))
        elif row["content_hash"] != plan.approval_hash or row["payload_json"] != payload:
            raise GovernanceApplyError("registered approval differs from supplied approval")
        self.manager.consume_in_transaction(
            connection,
            {
                "approval_id": plan.approval_id,
                "expected_action": _EXPECTED_ACTION,
                "expected_target_type": _EXPECTED_TYPE,
                "expected_target_id": plan.proposal_id,
                "expected_target_hash": plan.proposal_hash,
                "expected_scope": _EXPECTED_SCOPE,
            },
        )
        self._prepare_filesystem(plan)
        return {"proposal_id": plan.proposal_id, "proposal_hash": plan.proposal_hash, "approval_id": plan.approval_id, "operation_count": len(plan.replacements), "status": "applied"}

    def apply(self, proposal_source: str | Path, approval_source: str | Path) -> dict[str, Any]:
        recovery = self.recover()
        try:
            plan = self._load_plan(proposal_source, approval_source)
        except GovernanceApplyError:
            # A committed event is authoritative for idempotent replay.  The
            # proposal targets are now intentionally after-hash bytes, so the
            # normal before-hash preflight cannot be rerun.  Require the exact
            # proposal and approval identities/hashes recorded in the event.
            proposal_path = _safe_path(self.root, proposal_source, required_relative=_PROPOSAL_RELATIVE)
            approval_path = _safe_path(self.root, approval_source)
            proposal = _read_json(proposal_path)
            approval = _read_json(approval_path)
            proposal_id = proposal.get("proposal_id")
            approval_id = approval.get("approval_id")
            if not isinstance(proposal_id, str) or not isinstance(approval_id, str):
                raise
            key = f"governance-apply:{proposal_id}:{approval_id}"
            events = self.store.rows("SELECT result_json FROM events WHERE idempotency_key = ?", (key,))
            proposal_hash = sha256_of_json(proposal)
            approval_hash = sha256_of_json(approval)
            if not events:
                raise
            committed = json.loads(events[0]["result_json"])
            if committed.get("proposal_hash") != proposal_hash or committed.get("approval_id") != approval_id:
                raise GovernanceApplyError("committed replay identity does not match supplied artifacts")
            committed["recovery_before_apply"] = recovery["status"]
            return committed
        approval = _read_json(_safe_path(self.root, approval_source))
        event = make_event(event_type="governance_change_applied", reason_code="approved_governance_change_set", actor_role="control_plane", artifact_refs=[plan.proposal_id, plan.approval_id])
        try:
            result = self.store.execute(plan.idempotency_key, event, lambda connection: self._mutation(plan, approval, connection))
        except Exception:
            # The SQLite transaction has rolled back; recover the filesystem
            # before returning any failure to the caller.
            self.recover()
            raise
        if self.failure_injector:
            self.failure_injector("postcommit", -1)
        self.recover()
        result["recovery_before_apply"] = recovery["status"]
        return result
