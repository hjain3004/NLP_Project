"""Fail-closed application of the approved governance change-set proposal.

The proposal is data until this module verifies the fixed target, the complete
operation manifest, and a MAC-sealed approval.  Filesystem replacement is
recoverable and is performed inside the same serialized state mutation that
consumes the approval and appends the application event.
"""

from __future__ import annotations

import copy
import datetime as dt
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
from .errors import AgentctlError, InvalidArtifact
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
    approval_path: str
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


def _proposal_manifest(proposal: Mapping[str, Any]) -> tuple[str, dict[str, tuple[str, str]]]:
    """Validate proposal identity and return path -> before/after hashes."""
    if (
        proposal.get("artifact_type") != "governance-change-set-proposal"
        or proposal.get("status") != "proposed"
        or proposal.get("authoritative") is not False
    ):
        raise GovernanceApplyError("proposal is not a proposed non-authoritative governance change set")
    proposal_id = proposal.get("proposal_id")
    if not isinstance(proposal_id, str) or not proposal_id:
        raise GovernanceApplyError("proposal identity is malformed")
    operations = proposal.get("operations")
    if not isinstance(operations, list) or len(operations) != len(_EXPECTED_PATHS):
        raise GovernanceApplyError("governance proposal must contain exactly ten operations")
    paths = [item.get("path") for item in operations if isinstance(item, Mapping)]
    if len(paths) != len(set(paths)) or set(paths) != _EXPECTED_PATHS:
        raise GovernanceApplyError("governance proposal operation manifest is not exactly the approved ten paths")
    hashes: dict[str, tuple[str, str]] = {}
    for operation in operations:
        if not isinstance(operation, Mapping):
            raise GovernanceApplyError("governance operation is not an object")
        path = operation["path"]
        before = _assert_sha(operation.get("before_bytes_sha256"), field="before_bytes_sha256", path=path)
        if operation.get("encoding") == "canonical_json_utf8":
            after = _assert_sha(operation.get("after_canonical_json_sha256"), field="after_canonical_json_sha256", path=path)
        elif operation.get("encoding") == "utf8_patch":
            after = _assert_sha(operation.get("after_bytes_sha256"), field="after_bytes_sha256", path=path)
        else:
            raise GovernanceApplyError("proposal operation encoding is unknown", path=path)
        hashes[path] = (before, after)
    return proposal_id, hashes


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


def _require_nofollow() -> int:
    flag = getattr(os, "O_NOFOLLOW", None)
    if flag is None:
        raise GovernanceApplyError("platform lacks required no-follow filesystem support")
    return int(flag)


def _open_parent_directory(root: Path, relative: str | Path) -> int:
    """Open a target's parent chain without following symlinks."""
    nofollow = _require_nofollow()
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow
    if not getattr(os, "O_DIRECTORY", None):
        raise GovernanceApplyError("platform lacks required directory-descriptor support")
    descriptor = os.open(root, flags)
    try:
        for part in Path(relative).parent.parts:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _hash_fd(descriptor: int) -> str:
    digest = hashlib.sha256()
    while True:
        chunk = os.read(descriptor, 1 << 20)
        if not chunk:
            return digest.hexdigest()
        digest.update(chunk)


def _hash_name(parent_fd: int, name: str) -> str:
    nofollow = _require_nofollow()
    descriptor = os.open(name, os.O_RDONLY | nofollow, dir_fd=parent_fd)
    try:
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(mode):
            raise GovernanceApplyError("target is not a regular file", path=name)
        return _hash_fd(descriptor)
    finally:
        os.close(descriptor)


def _name_exists(parent_fd: int, name: str) -> bool:
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise GovernanceApplyError("filesystem transaction artifact is unsafe", path=name)
    return True


def _assert_name_hash(parent_fd: int, name: str, expected: str, *, allow_missing: bool = False) -> bool:
    if not _name_exists(parent_fd, name):
        if allow_missing:
            return False
        raise GovernanceApplyError("expected filesystem transaction file is missing", path=name)
    actual = _hash_name(parent_fd, name)
    if actual != expected:
        raise GovernanceApplyError("filesystem transaction hash mismatch", path=name)
    return True


def _write_name(parent_fd: int, name: str, content: bytes, mode: int) -> None:
    nofollow = _require_nofollow()
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, mode, dir_fd=parent_fd)
    try:
        os.fchmod(descriptor, mode)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise GovernanceApplyError("staging write made no progress", path=name)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _mode_name(parent_fd: int, name: str) -> int:
    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise GovernanceApplyError("target is not a regular file", path=name)
    return stat.S_IMODE(info.st_mode)


def _rename_name(parent_fd: int, source: str, destination: str, *, allow_existing: bool = False) -> None:
    _require_nofollow()
    if _name_exists(parent_fd, source) is False:
        raise GovernanceApplyError("rename source is missing", path=source)
    if _name_exists(parent_fd, destination) and not allow_existing:
        raise GovernanceApplyError("rename destination already exists", path=destination)
    os.replace(source, destination, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    os.fsync(parent_fd)


def _unlink_name(parent_fd: int, name: str) -> None:
    if not _name_exists(parent_fd, name):
        return
    os.unlink(name, dir_fd=parent_fd)
    os.fsync(parent_fd)


class GovernanceApplier:
    """Prepare, verify, apply, and recover one exact governance proposal."""

    def __init__(
        self,
        store: EventStore,
        *,
        key: bytes | None = None,
        key_path: str | os.PathLike[str] | None = None,
        failure_injector: Callable[[str, int], None] | None = None,
        now: dt.datetime | None = None,
    ) -> None:
        self.store = store
        self.root = store.repository_root.resolve()
        self.manager = ApprovalManager(store, key=key, key_path=key_path)
        self.failure_injector = failure_injector
        self.now = now
        self.journal_path = self.root / _JOURNAL_RELATIVE

    def _load_plan(self, proposal_source: str | Path, approval_source: str | Path) -> GovernancePlan:
        proposal_path = _safe_path(self.root, proposal_source, required_relative=_PROPOSAL_RELATIVE)
        approval_path = _safe_path(self.root, approval_source)
        try:
            approval_path.relative_to(self.root / _APPROVAL_DIRECTORY)
        except ValueError as exc:
            raise GovernanceApplyError("approval path is outside the fixed approval directory") from exc
        proposal = _read_json(proposal_path)
        proposal_id, _manifest = _proposal_manifest(proposal)
        operations = proposal["operations"]
        replacements = tuple(_replacement(self.root, operation) for operation in operations if isinstance(operation, Mapping))
        proposal_hash = sha256_of_json(proposal)
        approval = _read_json(approval_path)
        target = resolve_approval_target(_EXPECTED_TYPE, proposal_id, repository_root=self.root)
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
            expected_target_id=proposal_id,
            expected_target_hash=proposal_hash,
            expected_scope=_EXPECTED_SCOPE,
            policy=self.manager.policy,
            repository_root=self.root,
            now=self.now,
        )
        return GovernancePlan(
            proposal_id=proposal_id,
            proposal_hash=proposal_hash,
            approval_id=approval_id,
            approval_hash=sha256_of_json(approval),
            approval_path=str(approval_path.relative_to(self.root)),
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
            "approval_path": plan.approval_path,
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
        return dict(journal)

    def _validate_journal(self, journal: Mapping[str, Any]) -> dict[str, Any] | None:
        """Validate all journal claims and filesystem artifacts before mutation."""
        if journal.get("schema_version") != 1 or journal.get("state") not in {"preparing", "prepared", "applying", "applied"}:
            raise GovernanceApplyError("recovery journal state is malformed")
        proposal_path = _safe_path(self.root, _PROPOSAL_RELATIVE, required_relative=_PROPOSAL_RELATIVE)
        proposal = _read_json(proposal_path)
        proposal_id, manifest = _proposal_manifest(proposal)
        proposal_hash = sha256_of_json(proposal)
        if journal.get("proposal_id") != proposal_id or journal.get("proposal_hash") != proposal_hash:
            raise GovernanceApplyError("recovery journal proposal binding is invalid")
        approval_id = journal.get("approval_id")
        approval_hash = journal.get("approval_hash")
        approval_path_value = journal.get("approval_path")
        if not isinstance(approval_id, str) or not isinstance(approval_path_value, str):
            raise GovernanceApplyError("recovery journal approval binding is malformed")
        _assert_sha(approval_hash, field="approval_hash", path="journal")
        approval_path = _safe_path(self.root, approval_path_value)
        try:
            approval_relative = approval_path.relative_to(self.root / _APPROVAL_DIRECTORY)
        except ValueError as exc:
            raise GovernanceApplyError("recovery journal approval path is outside fixed directory") from exc
        approval = _read_json(approval_path)
        if approval.get("approval_id") != approval_id or sha256_of_json(approval) != approval_hash:
            raise GovernanceApplyError("recovery journal approval binding is invalid")
        expected_key = f"governance-apply:{proposal_id}:{approval_id}"
        if journal.get("idempotency_key") != expected_key:
            raise GovernanceApplyError("recovery journal idempotency binding is invalid")
        entries = journal.get("entries")
        if not isinstance(entries, list) or len(entries) != len(_EXPECTED_PATHS):
            raise GovernanceApplyError("recovery journal must contain exactly ten entries")
        seen: set[str] = set()
        token: str | None = None
        for entry in entries:
            if not isinstance(entry, Mapping) or not all(isinstance(entry.get(k), str) for k in ("path", "stage", "backup", "before_hash", "after_hash")):
                raise GovernanceApplyError("recovery journal entry is malformed")
            path = str(entry["path"])
            if path in seen or path not in manifest or Path(path).is_absolute() or ".." in Path(path).parts or Path(path).as_posix() != path:
                raise GovernanceApplyError("recovery journal target manifest is invalid", path=path)
            seen.add(path)
            before, after = manifest[path]
            if entry["before_hash"] != before or entry["after_hash"] != after:
                raise GovernanceApplyError("recovery journal target hashes do not match proposal", path=path)
            # A crash can occur after target->backup and before stage->target,
            # so the canonical target is allowed to be absent here.  Parent
            # components are opened no-follow below; the final name itself is
            # rejected if it is a symlink.
            target = self.root / path
            if target.is_symlink():
                raise GovernanceApplyError("recovery target is a symlink", path=path)
            target_name = target.name
            stage = Path(entry["stage"])
            backup = Path(entry["backup"])
            if stage.is_absolute() or backup.is_absolute() or ".." in stage.parts or ".." in backup.parts:
                raise GovernanceApplyError("recovery journal artifact path is invalid", path=path)
            if stage.parent.as_posix() != Path(path).parent.as_posix() or backup.parent.as_posix() != Path(path).parent.as_posix():
                raise GovernanceApplyError("recovery journal artifact is not adjacent to target", path=path)
            stage_parts = stage.name.rsplit("-", 1)
            backup_parts = backup.name.rsplit("-", 1)
            if len(stage_parts) != 2 or len(backup_parts) != 2 or stage_parts[0] != f".{target_name}.agentctl-stage" or backup_parts[0] != f".{target_name}.agentctl-backup":
                raise GovernanceApplyError("recovery journal artifact name is invalid", path=path)
            if len(stage_parts[1]) != 32 or any(c not in _HEX64 for c in stage_parts[1]) or backup_parts[1] != stage_parts[1]:
                raise GovernanceApplyError("recovery journal artifact token is invalid", path=path)
            if token is None:
                token = stage_parts[1]
            elif token != stage_parts[1]:
                raise GovernanceApplyError("recovery journal artifact tokens differ")
            parent_fd = _open_parent_directory(self.root, path)
            try:
                if _name_exists(parent_fd, target_name) and _hash_name(parent_fd, target_name) not in {before, after}:
                    raise GovernanceApplyError("recovery target hash is outside approved before/after states", path=path)
                _assert_name_hash(parent_fd, stage.name, after, allow_missing=True)
                _assert_name_hash(parent_fd, backup.name, before, allow_missing=True)
            finally:
                os.close(parent_fd)
        if seen != _EXPECTED_PATHS:
            raise GovernanceApplyError("recovery journal path set is incomplete")
        rows = self.store.rows(
            "SELECT event_type, reason_code, payload_json, result_json FROM events WHERE idempotency_key = ?",
            (expected_key,),
        )
        if len(rows) > 1:
            raise GovernanceApplyError("recovery event identity is ambiguous")
        if not rows:
            if self.store.scalar("SELECT COUNT(*) FROM artifacts WHERE artifact_id = ? AND artifact_type = 'human-approval'", (approval_id,)):
                raise GovernanceApplyError("recovery has an approval artifact without its committed event")
            return None
        row = rows[0]
        if row["event_type"] != "governance_change_applied" or row["reason_code"] != "approved_governance_change_set":
            raise GovernanceApplyError("recovery event is not the approved governance event")
        event = json.loads(row["payload_json"])
        result = json.loads(row["result_json"])
        expected_before = {path: values[0] for path, values in manifest.items()}
        expected_after = {path: values[1] for path, values in manifest.items()}
        if event.get("artifact_refs") != [proposal_id, approval_id] or result.get("proposal_id") != proposal_id or result.get("proposal_hash") != proposal_hash or result.get("approval_id") != approval_id or result.get("approval_hash") != approval_hash or result.get("before_hashes") != expected_before or result.get("after_hashes") != expected_after or result.get("operation_count") != len(_EXPECTED_PATHS):
            raise GovernanceApplyError("recovery event does not match approved result")
        return result

    def recover(self) -> dict[str, Any]:
        """Recover an interrupted transaction without consuming approval."""
        journal = self._read_journal()
        if journal is None:
            return {"status": "clean"}
        committed = self._validate_journal(journal)
        entries = journal["entries"]
        for entry in entries:
            parent_fd = _open_parent_directory(self.root, entry["path"])
            try:
                target_name = Path(entry["path"]).name
                stage_name = Path(entry["stage"]).name
                backup_name = Path(entry["backup"]).name
                if committed is not None:
                    target_after = _assert_name_hash(parent_fd, target_name, entry["after_hash"], allow_missing=True)
                    if not target_after:
                        _assert_name_hash(parent_fd, stage_name, entry["after_hash"])
                        _rename_name(parent_fd, stage_name, target_name, allow_existing=True)
                    _assert_name_hash(parent_fd, target_name, entry["after_hash"])
                else:
                    backup_present = _assert_name_hash(parent_fd, backup_name, entry["before_hash"], allow_missing=True)
                    target_present = _name_exists(parent_fd, target_name)
                    target_hash = _hash_name(parent_fd, target_name) if target_present else None
                    if target_hash not in {None, entry["before_hash"], entry["after_hash"]}:
                        raise GovernanceApplyError("recovery cannot prove the before tree", path=entry["path"])
                    if target_hash != entry["before_hash"]:
                        if not backup_present:
                            raise GovernanceApplyError("recovery cannot prove the before tree", path=entry["path"])
                        _rename_name(parent_fd, backup_name, target_name, allow_existing=True)
                    elif backup_present:
                        _rename_name(parent_fd, backup_name, target_name, allow_existing=True)
                    _assert_name_hash(parent_fd, target_name, entry["before_hash"])
            finally:
                os.close(parent_fd)
        self._cleanup_entries(entries)
        self.journal_path.unlink(missing_ok=True)
        _fsync_directory(self.journal_path.parent)
        if committed is not None:
            return {"status": "committed", "result": committed}
        return {"status": "rolled_back"}

    def _cleanup_entries(self, entries: list[Mapping[str, str]]) -> None:
        for entry in entries:
            parent_fd = _open_parent_directory(self.root, entry["path"])
            try:
                for key in ("stage", "backup"):
                    _unlink_name(parent_fd, Path(entry[key]).name)
            finally:
                os.close(parent_fd)

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

    def _historical_replay(
        self, proposal_source: str | Path, approval_source: str | Path, recovery: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """Return a read-only result only for an exact committed historical run."""
        proposal_path = _safe_path(self.root, proposal_source, required_relative=_PROPOSAL_RELATIVE)
        approval_path = _safe_path(self.root, approval_source)
        try:
            approval_relative = approval_path.relative_to(self.root / _APPROVAL_DIRECTORY)
        except ValueError as exc:
            raise GovernanceApplyError("approval path is outside the fixed approval directory") from exc
        proposal = _read_json(proposal_path)
        proposal_id, manifest = _proposal_manifest(proposal)
        proposal_hash = sha256_of_json(proposal)
        approval = _read_json(approval_path)
        approval_id = approval.get("approval_id")
        if not isinstance(approval_id, str) or not approval_id:
            raise GovernanceApplyError("approval identity is malformed")
        key = f"governance-apply:{proposal_id}:{approval_id}"
        rows = self.store.rows(
            "SELECT event_type, reason_code, payload_json, result_json FROM events "
            "WHERE idempotency_key = ?",
            (key,),
        )
        if not rows:
            return None
        row = rows[0]
        if row["event_type"] != "governance_change_applied" or row["reason_code"] != "approved_governance_change_set":
            raise GovernanceApplyError("historical event identity is not a governance application")
        event = json.loads(row["payload_json"])
        if event.get("artifact_refs") != [proposal_id, approval_id]:
            raise GovernanceApplyError("historical event artifact identity does not match")
        result = json.loads(row["result_json"])
        if (
            result.get("proposal_id") != proposal_id
            or result.get("proposal_hash") != proposal_hash
            or result.get("approval_id") != approval_id
            or result.get("approval_hash") != sha256_of_json(approval)
            or result.get("operation_count") != len(_EXPECTED_PATHS)
        ):
            raise GovernanceApplyError("historical result identity does not match supplied artifacts")
        expected_after = {path: after for path, (_before, after) in manifest.items()}
        if result.get("after_hashes") != expected_after:
            raise GovernanceApplyError("historical result replacement manifest is incomplete")
        if result.get("before_hashes") != {path: before for path, (before, _after) in manifest.items()}:
            raise GovernanceApplyError("historical result before manifest is incomplete")
        if approval_relative.as_posix() != result.get("approval_path", approval_relative.as_posix()):
            raise GovernanceApplyError("historical approval path does not match")
        if (
            approval.get("artifact_type") != "human-approval"
            or approval.get("authorized_action") != _EXPECTED_ACTION
            or approval.get("target_type") != _EXPECTED_TYPE
            or approval.get("target_id") != proposal_id
            or approval.get("target_hash") != proposal_hash
            or approval.get("scope") != _EXPECTED_SCOPE
            or approval.get("single_use") is not True
        ):
            raise GovernanceApplyError("historical approval binding does not match")
        artifact_rows = self.store.rows(
            "SELECT payload_json, content_hash FROM artifacts "
            "WHERE artifact_id = ? AND artifact_type = 'human-approval'",
            (approval_id,),
        )
        if len(artifact_rows) != 1:
            raise GovernanceApplyError("historical approval artifact is missing or ambiguous")
        stored = json.loads(artifact_rows[0]["payload_json"])
        if stored.get("consumed_at") is None:
            raise GovernanceApplyError("historical approval was not consumed")
        stored_for_original = dict(stored)
        stored_for_original["consumed_at"] = None
        if stored_for_original != dict(approval):
            raise GovernanceApplyError("historical approval content differs from stored approval")
        if sha256_of_json(stored) != artifact_rows[0]["content_hash"]:
            raise GovernanceApplyError("historical approval artifact hash is corrupt")
        if any(sha256_of_file(self.root / path) != after for path, after in expected_after.items()):
            raise GovernanceApplyError("canonical tree drifted from committed result")
        result["recovery_before_apply"] = recovery["status"]
        return result

    def _prepare_filesystem(self, plan: GovernancePlan) -> None:
        token = uuid.uuid4().hex
        entries: list[dict[str, str]] = []
        for replacement in plan.replacements:
            target = _safe_path(self.root, replacement.relative_path)
            parent_fd = _open_parent_directory(self.root, replacement.relative_path)
            try:
                target_name = Path(replacement.relative_path).name
                if _assert_name_hash(parent_fd, target_name, replacement.before_hash) is not True:
                    raise GovernanceApplyError("target changed before mutation", path=replacement.relative_path)
            finally:
                os.close(parent_fd)
            parent = Path(replacement.relative_path).parent
            entries.append({"path": replacement.relative_path, "stage": str(parent / f".{target.name}.agentctl-stage-{token}"), "backup": str(parent / f".{target.name}.agentctl-backup-{token}"), "before_hash": replacement.before_hash, "after_hash": replacement.after_hash})
        self._journal(plan, "preparing", entries)
        descriptors: list[int] = []
        try:
            for replacement, entry in zip(plan.replacements, entries):
                parent_fd = _open_parent_directory(self.root, entry["path"])
                descriptors.append(parent_fd)
                target_name = Path(entry["path"]).name
                stage_name = Path(entry["stage"]).name
                mode = _mode_name(parent_fd, target_name)
                _write_name(parent_fd, stage_name, replacement.replacement, mode)
                _assert_name_hash(parent_fd, stage_name, replacement.after_hash)
            for entry, parent_fd in zip(entries, descriptors):
                _assert_name_hash(parent_fd, Path(entry["path"]).name, entry["before_hash"])
        finally:
            for descriptor in descriptors:
                os.close(descriptor)
        self._journal(plan, "prepared", entries)
        if self.failure_injector:
            self.failure_injector("after_staging", -1)
        self._journal(plan, "applying", entries)
        for index, entry in enumerate(entries):
            parent_fd = _open_parent_directory(self.root, entry["path"])
            try:
                target_name = Path(entry["path"]).name
                stage_name = Path(entry["stage"]).name
                backup_name = Path(entry["backup"]).name
                _assert_name_hash(parent_fd, target_name, entry["before_hash"])
                _assert_name_hash(parent_fd, stage_name, entry["after_hash"])
                if _name_exists(parent_fd, backup_name):
                    raise GovernanceApplyError("stale backup artifact exists", path=entry["path"])
                _rename_name(parent_fd, target_name, backup_name)
                _rename_name(parent_fd, stage_name, target_name)
                _assert_name_hash(parent_fd, target_name, entry["after_hash"])
            finally:
                os.close(parent_fd)
            if self.failure_injector:
                self.failure_injector("mid_rename", index)
        for entry in entries:
            parent_fd = _open_parent_directory(self.root, entry["path"])
            try:
                _assert_name_hash(parent_fd, Path(entry["path"]).name, entry["after_hash"])
            finally:
                os.close(parent_fd)
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
            now=self.now,
        )
        self._prepare_filesystem(plan)
        return {
            "proposal_id": plan.proposal_id,
            "proposal_hash": plan.proposal_hash,
            "approval_id": plan.approval_id,
            "approval_hash": plan.approval_hash,
            "before_hashes": {item.relative_path: item.before_hash for item in plan.replacements},
            "after_hashes": {item.relative_path: item.after_hash for item in plan.replacements},
            "operation_count": len(plan.replacements),
            "status": "applied",
        }

    def apply(self, proposal_source: str | Path, approval_source: str | Path) -> dict[str, Any]:
        recovery = self.recover()
        try:
            plan = self._load_plan(proposal_source, approval_source)
        except AgentctlError as original_error:
            historical = self._historical_replay(proposal_source, approval_source, recovery)
            if historical is not None:
                return historical
            raise original_error
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
