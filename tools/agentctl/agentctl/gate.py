"""Phase-gate evidence re-execution and architecture-audit ingestion."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import hmac
import json
import os
import sqlite3
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .approval import ApprovalManager, ApprovalRejected, verify_approval
from .config import load as load_config
from .errors import InvalidArtifact, PermissionViolation, PlanningError
from .events import make_event
from .hashing import sha256_hex, sha256_of_file, sha256_of_json
from .routing import load_registry
from .store import EventStore
from .validate import validate_artifact

__all__ = ["GateEvaluator", "ingest_audit", "register_audit_dispatch", "seal_audit_dispatch"]

_G0_CLOSURE_PATHS = frozenset(
    {
        "agent-system/work/P0/gates/G0-result.json",
        "agent-system/work/P0/gates/G0-scope-statement.json",
        *(f"agent-system/work/P0/gates/G0-audit-pass-{number}.json" for number in range(1, 6)),
        "agent-system/work/P0/gates/G0-architecture-audit.json",
        "agent-system/work/P0/approvals/G0-scope-approval.json",
        "agent-system/work/P0/approvals/G0-phase-approval.json",
        "docs/program/PHASE_GATES.md",
    }
)


def _timestamp() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


_AUDIT_DISPATCH_TYPE = "architecture-audit-dispatch"
_AUDIT_OPERATIONAL_ROLE = "architectural_auditor"
_AUDIT_DISPATCH_SEAL = "control_plane_seal"


def seal_audit_dispatch(assignment: Mapping[str, Any], key: bytes) -> dict[str, Any]:
    """Seal a dispatch in the controller boundary before it enters runtime state."""
    if not isinstance(key, bytes) or not key:
        raise ValueError("audit dispatch seal key must be nonempty bytes")
    result = dict(assignment)
    result.pop(_AUDIT_DISPATCH_SEAL, None)
    result[_AUDIT_DISPATCH_SEAL] = hmac.new(
        key,
        json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return result


def _parse_audit_time(value: object, *, field: str) -> dt.datetime:
    if not isinstance(value, str):
        raise PermissionViolation("audit dispatch timestamp is absent", field=field)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PermissionViolation("audit dispatch timestamp is malformed", field=field) from exc
    if parsed.tzinfo is None:
        raise PermissionViolation("audit dispatch timestamp lacks timezone", field=field)
    return parsed.astimezone(dt.timezone.utc)


def _audit_dispatch_binding(
    assignment: Mapping[str, Any], *, root: Path, key: bytes, now: dt.datetime | None = None
) -> dict[str, Any]:
    required = (
        "artifact_type", "dispatch_id", "audit_id", "task_id", "attempt_id",
        "contract_hash", "context_hash", "gate_id", "pass_id", "audited_commit",
        "audited_tree_hash", "capability_class", "operational_role", "auditor_role", "auditor_provider",
        "auditor_model", "auditor_session_id", "auditor_run_id", "identity_evidence_ref",
        "identity_evidence_hash", "author_identities", "unknown_author_identities",
        "state", "issued_at", "expires_at", _AUDIT_DISPATCH_SEAL,
    )
    missing = [field for field in required if field not in assignment]
    if missing:
        raise PermissionViolation("audit dispatch evidence is incomplete", missing=missing)
    if assignment["artifact_type"] != _AUDIT_DISPATCH_TYPE:
        raise PermissionViolation("audit dispatch evidence has the wrong artifact type")
    unsigned = dict(assignment)
    supplied_seal = unsigned.pop(_AUDIT_DISPATCH_SEAL, None)
    expected_seal = seal_audit_dispatch(unsigned, key)[_AUDIT_DISPATCH_SEAL]
    if not isinstance(supplied_seal, str) or not hmac.compare_digest(supplied_seal, expected_seal):
        raise PermissionViolation("audit dispatch is not sealed by the control plane")
    if assignment["state"] != "active":
        raise PermissionViolation("audit dispatch assignment is not active", state=assignment["state"])
    if (
        assignment["gate_id"] != "G0"
        or assignment["operational_role"] != _AUDIT_OPERATIONAL_ROLE
        or assignment["auditor_role"] != "architectural_authority"
    ):
        raise PermissionViolation("audit dispatch role or gate is not authorized")
    if assignment["capability_class"] != "architectural_authority":
        raise PermissionViolation("audit dispatch capability is not architectural_authority")
    for field in (
        "dispatch_id", "audit_id", "task_id", "attempt_id", "contract_hash", "context_hash",
        "pass_id", "audited_commit", "audited_tree_hash", "auditor_provider", "auditor_model",
        "auditor_session_id", "auditor_run_id", "identity_evidence_ref", "identity_evidence_hash",
    ):
        if not isinstance(assignment[field], str) or not assignment[field]:
            raise PermissionViolation("audit dispatch identity binding is malformed", field=field)
    if not isinstance(assignment["author_identities"], list) or not all(
        isinstance(item, str) and item for item in assignment["author_identities"]
    ):
        raise PermissionViolation("audit dispatch author identities are malformed")
    if assignment["unknown_author_identities"] is not False:
        raise PermissionViolation("audit dispatch has unknown author identities")
    if assignment["auditor_session_id"] in assignment["author_identities"] or assignment["auditor_run_id"] in assignment["author_identities"]:
        raise PermissionViolation("audit dispatch auditor identity is an author identity")
    if len(assignment["identity_evidence_hash"]) != 64 or any(
        character not in "0123456789abcdef" for character in assignment["identity_evidence_hash"]
    ):
        raise PermissionViolation("audit dispatch identity evidence hash is malformed")
    current = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    if _parse_audit_time(assignment["expires_at"], field="expires_at") <= current:
        raise PermissionViolation("audit dispatch assignment has expired")
    _parse_audit_time(assignment["issued_at"], field="issued_at")
    try:
        tree = subprocess.check_output(
            ["git", "rev-parse", f"{assignment['audited_commit']}^{{tree}}"],
            cwd=root, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PermissionViolation("audit dispatch target is not a repository commit") from exc
    if tree != assignment["audited_tree_hash"]:
        raise PermissionViolation("audit dispatch target tree does not match the frozen target")
    registry = load_registry(root).get("architectural_authority")
    if registry is None or registry.get("provider") != assignment["auditor_provider"] or registry.get("model_id") != assignment["auditor_model"]:
        raise PermissionViolation("audit dispatch model is not the registered architectural authority")
    return dict(assignment)


def register_audit_dispatch(
    store: EventStore, assignment: Mapping[str, Any], *, source_path: str = "", key: bytes
) -> dict[str, Any]:
    """Record one controller-owned, identity-bound manual audit assignment."""
    binding = _audit_dispatch_binding(assignment, root=store.repository_root, key=key)
    current_head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=store.repository_root, text=True
    ).strip()
    if current_head != binding["audited_commit"]:
        raise PermissionViolation("audit dispatch must be registered at the frozen repository head")
    artifact_id = f"audit-dispatch:{binding['dispatch_id']}"
    event = make_event(
        event_type="architecture_audit_dispatched",
        reason_code="trusted_audit_assignment",
        actor_role="control_plane",
        phase_id="G0",
        repository_commits=[binding["audited_commit"]],
        artifact_refs=[artifact_id, binding["audit_id"]],
    )

    def mutation(connection: sqlite3.Connection) -> dict[str, Any]:
        existing = connection.execute(
            "SELECT artifact_id FROM artifacts WHERE artifact_type = ? AND integrable = 1 "
            "AND json_extract(payload_json, '$.audit_id') = ?",
            (_AUDIT_DISPATCH_TYPE, binding["audit_id"]),
        ).fetchone()
        if existing:
            raise PermissionViolation("an active controller dispatch already exists for this audit")
        connection.execute(
            "INSERT INTO artifacts(artifact_id, artifact_type, task_id, attempt_id, content_hash, path, integrable, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
            (
                artifact_id, _AUDIT_DISPATCH_TYPE, binding["task_id"], binding["attempt_id"],
                sha256_of_json(binding), source_path,
                json.dumps(binding, sort_keys=True, separators=(",", ":")),
            ),
        )
        return {"dispatch_id": binding["dispatch_id"], "audit_id": binding["audit_id"], "state": "active"}

    return dict(store.execute(f"audit-dispatch:{binding['dispatch_id']}", event, mutation))


class GateEvaluator:
    """Recompute every criterion and write a separate result artifact."""

    def __init__(
        self,
        repository_root: str | os.PathLike[str] | None = None,
        *,
        approval_key: bytes | None = None,
        store: EventStore | None = None,
        command_timeout_seconds: int = 300,
    ) -> None:
        self.root = (
            Path(repository_root).resolve()
            if repository_root is not None
            else load_config().repo_root
        )
        self.approval_key = approval_key
        self.store = store
        self.command_timeout_seconds = command_timeout_seconds

    def _path(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise PlanningError("gate evidence path escapes trusted base", path=relative) from exc
        return candidate

    def _approval_expectations(
        self,
        record: Mapping[str, Any],
        *,
        gate_id: str,
        criteria_path: Path,
    ) -> dict[str, str]:
        action = record.get("authorized_action")
        if action == "phase_authorization":
            return {
                "expected_action": action,
                "expected_target_type": "phase_gate",
                "expected_target_id": gate_id,
                "expected_target_hash": sha256_of_json(
                    json.loads(criteria_path.read_text(encoding="utf-8"))
                ),
                "expected_scope": str(record.get("scope", "")),
            }
        if action == "scope_authorization":
            statement = criteria_path.with_name(f"{gate_id}-scope-statement.json")
            if not statement.is_file():
                raise ApprovalRejected(
                    "approval_wrong_target_hash",
                    "scope approval target statement is absent",
                    path=str(statement),
                )
            return {
                "expected_action": action,
                "expected_target_type": "document_set",
                "expected_target_id": f"{gate_id}-scope",
                "expected_target_hash": sha256_of_json(
                    json.loads(statement.read_text(encoding="utf-8"))
                ),
                "expected_scope": str(record.get("scope", "")),
            }
        raise ApprovalRejected(
            "approval_wrong_action",
            "approval action cannot close a phase gate",
            action=action,
        )

    def _evaluate_criterion(
        self,
        source: Mapping[str, Any],
        *,
        gate_id: str,
        criteria_path: Path,
        persist_audit: bool = True,
    ) -> dict[str, Any]:
        criterion = {key: copy.deepcopy(value) for key, value in source.items() if key != "result"}
        evidence_type = criterion.get("evidence_type")
        result = "open"
        if evidence_type == "deterministic_check":
            command = criterion.get("evidence_command")
            if not isinstance(command, str) or not command:
                result = "fail"
            else:
                try:
                    completed = (
                        self._nested_git_check()
                        if criterion.get("criterion_id") == "G0-03"
                        else self._run_from_clean_checkout(command)
                        if criterion.get("criterion_id") == "G0-11"
                        else subprocess.run(
                            command,
                            cwd=self.root,
                            shell=True,
                            executable="/bin/sh",
                            capture_output=True,
                            timeout=self.command_timeout_seconds,
                        )
                    )
                except (OSError, subprocess.TimeoutExpired):
                    result = "fail"
                else:
                    expected_exit = int(criterion.get("expected_exit_code", 0))
                    exit_ok = completed.returncode == expected_exit
                    expected_hash = criterion.get("evidence_hash")
                    hash_ok = expected_hash is None or sha256_hex(completed.stdout) == expected_hash
                    result = "pass" if exit_ok and hash_ok else "fail"
        elif evidence_type == "artifact":
            reference = criterion.get("evidence_ref")
            if isinstance(reference, str) and reference:
                path = self._path(reference)
                expected_hash = criterion.get("evidence_hash")
                if not path.is_file():
                    result = "open"
                else:
                    try:
                        artifact = validate_artifact(path, repository_root=self.root)
                    except InvalidArtifact:
                        result = "fail"
                    else:
                        if artifact.get("artifact_type") == "architecture-audit":
                            try:
                                ingested = ingest_audit(
                                    gate_id,
                                    artifact,
                                    construction_log=(
                                        self.root
                                        / "agent-system/work/P0/CONSTRUCTION-LOG.md"
                                    ),
                                    store=self.store if persist_audit else None,
                                    source_path=reference,
                                    repository_root=self.root,
                                )
                            except (InvalidArtifact, PermissionViolation, subprocess.SubprocessError):
                                result = "fail"
                            else:
                                result = (
                                    "pass"
                                    if ingested["verdict"] == "no_blocking_findings"
                                    else "fail"
                                )
                            return {**criterion, "result": result}
                        identity_bound = any(
                            key in artifact
                            for key in ("task_id", "gate_id", "audit_id", "integration_id")
                        )
                        hash_ok = expected_hash is None or (
                            isinstance(expected_hash, str)
                            and sha256_of_file(path) == expected_hash
                        )
                        result = "pass" if identity_bound and hash_ok else "fail"
            else:
                result = "fail"
        elif evidence_type == "manual_attestation":
            reference = criterion.get("evidence_ref")
            if isinstance(reference, str) and reference and self._path(reference).is_file():
                path = self._path(reference)
                try:
                    artifact = validate_artifact(path, repository_root=self.root)
                except InvalidArtifact:
                    result = "fail"
                else:
                    identity_bound = any(
                        key in artifact
                        for key in ("task_id", "gate_id", "approval_id", "audit_id")
                    )
                    expected_hash = criterion.get("evidence_hash")
                    hash_ok = expected_hash is None or sha256_of_file(path) == expected_hash
                    result = "pass" if identity_bound and hash_ok else "fail"
            else:
                result = "open"
        elif evidence_type == "human_approval":
            reference = criterion.get("evidence_ref")
            if not isinstance(reference, str) or not reference or not self._path(reference).is_file():
                result = "open"
            else:
                record = json.loads(self._path(reference).read_text(encoding="utf-8"))
                key = self.approval_key
                if key is None:
                    try:
                        key = load_config().seal_key_path.read_bytes()
                    except OSError:
                        key = None
                if key is None:
                    result = "open"
                else:
                    try:
                        expectations = self._approval_expectations(
                            record, gate_id=gate_id, criteria_path=criteria_path
                        )
                        verify_approval(record, key, **expectations)
                    except Exception:
                        result = "fail"
                    else:
                        result = "pass"
        else:
            raise PlanningError("unknown gate evidence type", evidence_type=evidence_type)
        criterion["result"] = result
        return criterion

    def _nested_git_check(self) -> subprocess.CompletedProcess[bytes]:
        markers: list[str] = []
        for directory, dirnames, filenames in os.walk(self.root):
            current = Path(directory)
            if current == self.root and ".git" in dirnames:
                dirnames.remove(".git")
            if current != self.root and ".git" in dirnames:
                markers.append((current / ".git").relative_to(self.root).as_posix())
                dirnames.remove(".git")
            if current != self.root and ".git" in filenames:
                markers.append((current / ".git").relative_to(self.root).as_posix())
        staged = subprocess.run(
            ["git", "ls-files", "--stage", "-z"],
            cwd=self.root,
            capture_output=True,
            check=False,
        )
        gitlinks = []
        if staged.returncode == 0:
            gitlinks = [
                entry.decode("utf-8", errors="replace")
                for entry in staged.stdout.split(b"\0")
                if entry.startswith(b"160000 ")
            ]
        passed = staged.returncode == 0 and not markers and not gitlinks
        detail = "\n".join(sorted(markers + gitlinks)).encode("utf-8")
        return subprocess.CompletedProcess(
            args=["agentctl", "repository", "verify-no-nested-git"],
            returncode=0 if passed else 1,
            stdout=b"" if passed else detail,
            stderr=b"",
        )

    def _run_from_clean_checkout(
        self, command: str
    ) -> subprocess.CompletedProcess[bytes]:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.root, text=True
        ).strip()
        with tempfile.TemporaryDirectory(prefix="agentctl-gate-clean-") as temporary:
            checkout = Path(temporary) / "repository"
            subprocess.run(
                ["git", "clone", "--quiet", "--shared", str(self.root), str(checkout)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "checkout", "--quiet", "--detach", head],
                cwd=checkout,
                check=True,
                capture_output=True,
            )
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(checkout / "tools/agentctl")
            completed = subprocess.run(
                command,
                cwd=checkout,
                shell=True,
                executable="/bin/sh",
                capture_output=True,
                timeout=self.command_timeout_seconds,
                env=environment,
            )
            actual = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=checkout, text=True
            ).strip()
            if actual != head:
                raise PlanningError(
                    "clean-checkout evidence ran against another commit",
                    expected=head,
                    actual=actual,
                )
            return completed

    def _is_g0_closure_replay(
        self,
        source: Mapping[str, Any],
        *,
        source_path: Path,
        criteria_hash: str,
        head: str,
        approval_requests: tuple[Mapping[str, str], ...],
        all_criteria_recomputed: bool,
    ) -> bool:
        """Prove a post-commit G0 verification is the original closure replay."""
        if (
            self.store is None
            or source.get("gate_id") != "G0"
            or not approval_requests
            or not all_criteria_recomputed
        ):
            return False
        candidates = self.store.rows(
            "SELECT artifact_id, content_hash, payload_json FROM artifacts "
            "WHERE artifact_type = 'phase-gate' AND integrable = 1 "
            "AND artifact_id LIKE ? ORDER BY rowid DESC",
            (f"gate:G0:{criteria_hash}:%",),
        )
        for candidate in candidates:
            try:
                prior = json.loads(candidate["payload_json"])
                prior_commit = str(prior["evaluated_against_commit"])
                if prior.get("gate_id") != "G0" or prior.get("gate_result") != "passed":
                    continue
                if candidate["artifact_id"] != f"gate:G0:{criteria_hash}:{prior_commit}":
                    continue
                if candidate["content_hash"] != sha256_of_json(prior):
                    continue
                if prior_commit == head:
                    continue
                prior_events = self.store.rows(
                    "SELECT result_json, payload_json FROM events WHERE idempotency_key = ? "
                    "AND event_type = 'phase_gate_evaluated' AND reason_code = 'gate_passed'",
                    (f"gate-evaluate:G0:{criteria_hash}:{prior_commit}",),
                )
                if len(prior_events) != 1:
                    continue
                event_result = json.loads(prior_events[0]["result_json"])
                if event_result != {"gate_id": "G0", "gate_result": "passed"}:
                    continue
                closure_event = json.loads(prior_events[0]["payload_json"])
                expected_refs = [
                    candidate["artifact_id"],
                    *(str(request["approval_id"]) for request in approval_requests),
                ]
                if closure_event.get("artifact_refs") != expected_refs:
                    continue
                ancestor = subprocess.run(
                    ["git", "merge-base", "--is-ancestor", prior_commit, head],
                    cwd=self.root,
                    capture_output=True,
                    check=False,
                )
                if ancestor.returncode != 0:
                    continue
                changed = subprocess.run(
                    ["git", "diff", "--name-only", "--no-renames", prior_commit, head],
                    cwd=self.root,
                    capture_output=True,
                    check=True,
                ).stdout.decode("utf-8").splitlines()
                if not changed or any(path not in _G0_CLOSURE_PATHS for path in changed):
                    continue
                worktree = subprocess.run(
                    ["git", "status", "--porcelain", "--untracked-files=all"],
                    cwd=self.root,
                    capture_output=True,
                    check=True,
                )
                if worktree.stdout:
                    continue

                refs = list(source.get("human_approval_refs", []))
                if prior.get("human_approval_refs") != refs:
                    continue
                prior_criteria = prior.get("criteria")
                if not isinstance(prior_criteria, list) or any(
                    item.get("result") != "pass"
                    for item in prior_criteria
                    if item.get("evidence_type") == "human_approval"
                ):
                    continue
                source_criteria = [
                    {key: value for key, value in item.items() if key != "result"}
                    for item in source.get("criteria", [])
                ]
                if [
                    {key: value for key, value in item.items() if key != "result"}
                    for item in prior_criteria
                ] != source_criteria:
                    continue
                if sum(
                    item.get("evidence_type") == "human_approval"
                    for item in prior_criteria
                ) != len(approval_requests):
                    continue

                # Approval source records must be the exact committed records
                # the original gate could have evaluated, and their registered
                # authoritative copies must show consumption by that closure.
                committed_approval_paths: set[str] = set()
                for request in approval_requests:
                    approval_id = str(request["approval_id"])
                    rows = self.store.rows(
                        "SELECT path, payload_json FROM artifacts "
                        "WHERE artifact_id = ? AND artifact_type = 'human-approval'",
                        (approval_id,),
                    )
                    if len(rows) != 1:
                        break
                    registered = json.loads(rows[0]["payload_json"])
                    relative = str(rows[0]["path"])
                    if relative not in refs or relative not in _G0_CLOSURE_PATHS:
                        break
                    current_path = self._path(relative)
                    if not current_path.is_file():
                        break
                    head_blob = subprocess.run(
                        ["git", "show", f"{head}:{relative}"],
                        cwd=self.root,
                        capture_output=True,
                        check=True,
                    ).stdout
                    if (
                        current_path.read_bytes() != head_blob
                        or registered.get("approval_id") != approval_id
                        or registered.get("consumed_at") is None
                        or registered.get("revoked_at") is not None
                    ):
                        break
                    expected_fields = (
                        "approval_id",
                        "authorized_action",
                        "target_type",
                        "target_id",
                        "target_hash",
                        "scope",
                        "control_plane_seal",
                    )
                    current_record = json.loads(current_path.read_text(encoding="utf-8"))
                    if any(
                        current_record.get(field) != registered.get(field)
                        for field in expected_fields
                    ):
                        break
                    key = self.approval_key
                    if key is None:
                        try:
                            key = load_config().seal_key_path.read_bytes()
                        except OSError:
                            key = None
                    if key is None:
                        break
                    try:
                        verify_approval(
                            registered,
                            key,
                            **{
                                key: value
                                for key, value in request.items()
                                if key.startswith("expected_")
                            },
                            repository_root=self.root,
                        )
                    except ApprovalRejected as exc:
                        if exc.code != "approval_consumed":
                            break
                    else:
                        break
                    committed_approval_paths.add(relative)
                if len(committed_approval_paths) != len(approval_requests):
                    continue

                criteria_relative = source_path.relative_to(self.root).as_posix()
                if criteria_relative not in _G0_CLOSURE_PATHS:
                    # The immutable criteria are not closure evidence, so any
                    # legitimate G0 criteria file must predate the replay base.
                    if subprocess.run(
                        ["git", "diff", "--quiet", prior_commit, head, "--", criteria_relative],
                        cwd=self.root,
                        check=False,
                    ).returncode != 0:
                        continue
                return True
            except (KeyError, OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError):
                continue
        return False

    def _has_prior_passed_g0_result(
        self, criteria_hash: str | None, head: str
    ) -> bool:
        if self.store is None:
            return False
        artifact_pattern = (
            f"gate:G0:{criteria_hash}:%" if criteria_hash is not None else "gate:G0:%"
        )
        for row in self.store.rows(
            "SELECT payload_json FROM artifacts WHERE artifact_type = 'phase-gate' "
            "AND integrable = 1 AND artifact_id LIKE ?",
            (artifact_pattern,),
        ):
            try:
                result = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if (
                result.get("gate_id") == "G0"
                and result.get("gate_result") == "passed"
                and result.get("evaluated_against_commit") != head
            ):
                return True
        return False

    def _has_passed_g0_with_different_criteria(self, criteria_hash: str) -> bool:
        """Prevent a second G0 closure under altered criteria, even at the same HEAD."""
        if self.store is None:
            return False
        rows = self.store.rows(
            "SELECT artifact_id, content_hash, payload_json FROM artifacts "
            "WHERE artifact_type = 'phase-gate' AND integrable = 1"
        )
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
                parts = str(row["artifact_id"]).split(":", 3)
                if (
                    len(parts) != 4
                    or parts[0:2] != ["gate", "G0"]
                    or payload.get("gate_id") != "G0"
                    or payload.get("gate_result") != "passed"
                    or row["content_hash"] != sha256_of_json(payload)
                    or parts[3] != str(payload.get("evaluated_against_commit"))
                ):
                    continue
                if parts[2] != criteria_hash:
                    return True
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return False

    def _is_same_head_g0_result(
        self,
        source: Mapping[str, Any],
        *,
        artifact_id: str,
        criteria_hash: str,
        head: str,
        approval_requests: tuple[Mapping[str, str], ...],
        all_criteria_recomputed: bool,
    ) -> bool:
        """Recognize an existing same-HEAD close, including legacy event refs."""
        if self.store is None or source.get("gate_id") != "G0" or not all_criteria_recomputed:
            return False
        rows = self.store.rows(
            "SELECT content_hash, payload_json FROM artifacts WHERE artifact_id = ? "
            "AND artifact_type = 'phase-gate' AND integrable = 1",
            (artifact_id,),
        )
        if len(rows) != 1:
            return False
        try:
            prior = json.loads(rows[0]["payload_json"])
            if (
                rows[0]["content_hash"] != sha256_of_json(prior)
                or prior.get("gate_id") != "G0"
                or prior.get("gate_result") != "passed"
                or prior.get("evaluated_against_commit") != head
                or artifact_id != f"gate:G0:{criteria_hash}:{head}"
                or prior.get("human_approval_refs") != list(source.get("human_approval_refs", []))
            ):
                return False
            prior_criteria = prior.get("criteria")
            current_criteria = source.get("criteria", [])
            if not isinstance(prior_criteria, list) or [
                {key: value for key, value in item.items() if key != "result"}
                for item in prior_criteria
            ] != [
                {key: value for key, value in item.items() if key != "result"}
                for item in current_criteria
            ]:
                return False
            events = self.store.rows(
                "SELECT result_json, payload_json FROM events WHERE idempotency_key = ? "
                "AND event_type = 'phase_gate_evaluated' AND reason_code = 'gate_passed'",
                (f"gate-evaluate:G0:{criteria_hash}:{head}",),
            )
            if len(events) != 1:
                return False
            if json.loads(events[0]["result_json"]) != {"gate_id": "G0", "gate_result": "passed"}:
                return False
            refs = json.loads(events[0]["payload_json"]).get("artifact_refs")
            current_refs = [artifact_id, *(str(item["approval_id"]) for item in approval_requests)]
            return refs == [artifact_id] or refs == current_refs
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False

    def evaluate(
        self,
        criteria_path: str | os.PathLike[str],
        *,
        result_path: str | os.PathLike[str] | None = None,
        write_result: bool = True,
        evidence_types: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        source_path = Path(criteria_path)
        if not source_path.is_absolute():
            source_path = self._path(source_path.as_posix())
        else:
            source_path = source_path.resolve()
        source = json.loads(source_path.read_text(encoding="utf-8"))
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        head = commit.stdout.strip() if commit.returncode == 0 else "0" * 40
        criteria_hash = sha256_of_json(source)
        if source.get("gate_id") == "G0" and self._has_passed_g0_with_different_criteria(
            criteria_hash
        ):
            raise ApprovalRejected(
                "approval_replay_rejected",
                "G0 criteria differ from an already-passed authoritative closure",
                gate_id="G0",
            )
        if result_path is None:
            name = source_path.name.replace("-criteria.json", "-result.json")
            if name == source_path.name:
                name = f"{source['gate_id']}-result.json"
            destination = source_path.with_name(name)
        else:
            destination = Path(result_path)
            if not destination.is_absolute():
                destination = self._path(destination.as_posix())
        artifact_id = f"gate:{source['gate_id']}:{criteria_hash}:{head}"
        if write_result and self.store is not None and not destination.is_file():
            existing = self.store.rows(
                "SELECT payload_json FROM artifacts WHERE artifact_id = ? "
                "AND artifact_type = 'phase-gate'",
                (artifact_id,),
            )
            if existing:
                recovered = json.loads(existing[0]["payload_json"])
                destination.write_text(
                    json.dumps(recovered, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8",
                )
                return recovered
        selected_criteria = [
            item
            for item in source.get("criteria", [])
            if evidence_types is None or item.get("evidence_type") in evidence_types
        ]
        replay_candidate = (
            source.get("gate_id") == "G0"
            and (
                self._has_prior_passed_g0_result(None, head)
                or bool(
                    self.store is not None
                    and self.store.rows(
                        "SELECT 1 FROM artifacts WHERE artifact_id = ? "
                        "AND artifact_type = 'phase-gate' AND integrable = 1",
                        (artifact_id,),
                    )
                )
            )
        )
        criteria = [
            self._evaluate_criterion(
                item,
                gate_id=str(source.get("gate_id", "")),
                criteria_path=source_path,
                persist_audit=not replay_candidate,
            )
            for item in selected_criteria
        ]
        if not criteria:
            raise PlanningError("phase gate has no criteria", path=str(source_path))
        results = {item["result"] for item in criteria}
        gate_result = "failed" if "fail" in results else "open" if "open" in results else "passed"
        if (
            gate_result != "passed"
            and source.get("gate_id") == "G0"
            and self._has_prior_passed_g0_result(None, head)
        ):
            raise ApprovalRejected(
                "approval_replay_rejected",
                "fresh G0 replay criteria did not pass",
                gate_id="G0",
            )
        result: dict[str, Any] = {
            "artifact_type": "phase-gate",
            "gate_id": source["gate_id"],
            "phase_id": source["phase_id"],
            "evaluated_at": _timestamp(),
            "evaluated_against_commit": head,
            "criteria": criteria,
            "architecture_audit_ref": source.get("architecture_audit_ref"),
            "human_approval_refs": list(source.get("human_approval_refs", [])),
            "gate_result": gate_result,
        }
        approval_requests: list[dict[str, str]] = []
        if gate_result == "passed":
            for criterion in selected_criteria:
                if criterion.get("evidence_type") != "human_approval":
                    continue
                reference = criterion.get("evidence_ref")
                if not isinstance(reference, str) or not reference:
                    continue
                record = json.loads(self._path(reference).read_text(encoding="utf-8"))
                expectations = self._approval_expectations(
                    record,
                    gate_id=str(source["gate_id"]),
                    criteria_path=source_path,
                )
                approval_requests.append(
                    {"approval_id": str(record["approval_id"]), **expectations}
                )
        if gate_result == "passed" and source.get("gate_id") == "G0":
            all_criteria_recomputed = (
                evidence_types is None
                and len(selected_criteria) == len(source.get("criteria", []))
                and [
                    {key: value for key, value in item.items() if key != "result"}
                    for item in criteria
                ]
                == [
                    {key: value for key, value in item.items() if key != "result"}
                    for item in source.get("criteria", [])
                ]
            )
            if self._is_same_head_g0_result(
                source,
                artifact_id=artifact_id,
                criteria_hash=criteria_hash,
                head=head,
                approval_requests=tuple(approval_requests),
                all_criteria_recomputed=all_criteria_recomputed,
            ):
                return result
            if self._is_g0_closure_replay(
                source,
                source_path=source_path,
                criteria_hash=criteria_hash,
                head=head,
                approval_requests=tuple(approval_requests),
                all_criteria_recomputed=all_criteria_recomputed,
            ):
                return result
            if self._has_prior_passed_g0_result(None, head):
                raise ApprovalRejected(
                    "approval_replay_rejected",
                    "post-commit G0 replay does not match the original closure evidence",
                    gate_id="G0",
                )
        if self.store is not None:
            self._record(
                result,
                destination,
                artifact_id=artifact_id,
                criteria_hash=criteria_hash,
                approval_requests=tuple(approval_requests),
            )
        if write_result:
            destination.write_text(
                json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8"
            )
        return result

    def _record(
        self,
        result: Mapping[str, Any],
        destination: Path,
        *,
        artifact_id: str,
        criteria_hash: str,
        approval_requests: tuple[Mapping[str, str], ...],
    ) -> None:
        assert self.store is not None
        event = make_event(
            event_type="phase_gate_evaluated",
            reason_code=f"gate_{result['gate_result']}",
            actor_role="control_plane",
            phase_id=result["phase_id"],
            repository_commits=[result["evaluated_against_commit"]],
            artifact_refs=[
                artifact_id,
                *(
                    str(request["approval_id"])
                    for request in approval_requests
                    if result["gate_id"] == "G0"
                ),
            ],
        )

        def mutation(connection: sqlite3.Connection) -> dict[str, Any]:
            if approval_requests:
                manager = ApprovalManager(self.store, key=self.approval_key)
                for request in approval_requests:
                    manager.consume_in_transaction(connection, request)
            connection.execute(
                "INSERT INTO artifacts(artifact_id, artifact_type, content_hash, path, "
                "integrable, payload_json) VALUES (?, 'phase-gate', ?, ?, ?, ?)",
                (
                    artifact_id,
                    sha256_of_json(result),
                    destination.relative_to(self.root).as_posix(),
                    1 if result["gate_result"] == "passed" else 0,
                    json.dumps(dict(result), sort_keys=True, separators=(",", ":")),
                ),
            )
            return {"gate_id": result["gate_id"], "gate_result": result["gate_result"]}

        self.store.execute(
            f"gate-evaluate:{result['gate_id']}:{criteria_hash}:"
            f"{result['evaluated_against_commit']}",
            event,
            mutation,
        )


def ingest_audit(
    gate_id: str,
    audit: Mapping[str, Any],
    *,
    construction_log: str | os.PathLike[str],
    store: EventStore | None = None,
    source_path: str = "",
    repository_root: str | os.PathLike[str] | None = None,
    dispatch_key: bytes | None = None,
) -> dict[str, Any]:
    """Reject construction authors and optionally persist an architecture audit."""
    if audit.get("artifact_type") != "architecture-audit" or audit.get("gate_id") != gate_id:
        raise InvalidArtifact("architecture audit does not bind the requested gate")
    log_path = Path(construction_log)
    log = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    for field in ("auditor_session_id", "auditor_run_id"):
        identity = audit.get(field)
        if isinstance(identity, str) and identity and identity in log:
            raise PermissionViolation(
                "architecture auditor participated in P0 construction",
                field=field,
                identity=identity,
            )
    attestation = audit.get("independence_attestation", {})
    if attestation.get("fresh_session") is not True or attestation.get("distinct") is not True:
        raise PermissionViolation(
            "architecture audit lacks independent fresh-session attestation"
        )
    if not audit.get("artifact_families") or not audit.get("decision_coverage"):
        raise InvalidArtifact("architecture audit scope and decision coverage must be nonempty")
    has_blocking = any(
        finding.get("blocking") is True for finding in audit.get("findings", [])
    )
    verdict = audit.get("verdict")
    if (verdict == "no_blocking_findings" and has_blocking) or (
        verdict == "blocking_findings" and not has_blocking
    ):
        raise InvalidArtifact("architecture audit verdict contradicts its findings")

    if repository_root is None:
        if store is not None:
            repository_root = store.repository_root
        else:
            repository_root = subprocess.check_output(
                ["git", "-C", str(log_path.parent), "rev-parse", "--show-toplevel"],
                text=True,
            ).strip()
    root = Path(repository_root).resolve()
    expected_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    audited_commit = str(audit.get("audited_commit", ""))
    try:
        audited_tree = subprocess.check_output(
            ["git", "rev-parse", f"{audited_commit}^{{tree}}"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", audited_commit, expected_commit],
            cwd=root,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        raise InvalidArtifact(
            "architecture audit commit is absent or not an ancestor of the evaluated tree",
            audited_commit=audited_commit,
            evaluated_commit=expected_commit,
        ) from None
    if audit.get("audited_tree_hash") != audited_tree:
        raise InvalidArtifact(
            "architecture audit tree hash does not match its audited commit",
            audited_commit=audited_commit,
            expected_tree=audited_tree,
            audited_tree_hash=audit.get("audited_tree_hash"),
        )
    if audited_commit != expected_commit:
        changed = subprocess.check_output(
            ["git", "diff", "--name-only", f"{audited_commit}..{expected_commit}"],
            cwd=root,
            text=True,
        ).splitlines()
        allowed_exact = {
            "agent-system/work/P0/gates/G0-architecture-audit.json",
            "agent-system/work/P0/gates/G0-result.json",
            "docs/program/PHASE_GATES.md",
        }
        disallowed = [
            path
            for path in changed
            if path not in allowed_exact
            and not (
                path.startswith("agent-system/work/P0/gates/G0-audit-pass-")
                and path.endswith(".json")
            )
            and not (
                path.startswith("agent-system/work/P0/approvals/G0-")
                and path.endswith(".json")
            )
        ]
        if disallowed:
            raise InvalidArtifact(
                "architecture audit is stale because non-evidence files changed",
                audited_commit=audited_commit,
                evaluated_commit=expected_commit,
                disallowed_paths=disallowed,
            )
    if store is not None:
        dispatch_rows = store.rows(
            "SELECT payload_json FROM artifacts WHERE artifact_type = ? AND integrable = 1 "
            "ORDER BY rowid DESC",
            (_AUDIT_DISPATCH_TYPE,),
        )
        assignments = [
            json.loads(row["payload_json"])
            for row in dispatch_rows
            if json.loads(row["payload_json"]).get("audit_id") == audit.get("audit_id")
        ]
        if not assignments:
            raise PermissionViolation("architecture audit has no trusted controller dispatch assignment")
        if dispatch_key is None:
            raise PermissionViolation("controller dispatch verification key is required")
        assignment = _audit_dispatch_binding(
            assignments[0], root=Path(repository_root or store.repository_root), key=dispatch_key
        )
        for field in (
            "audit_id", "gate_id", "pass_id", "auditor_role", "auditor_provider", "auditor_model",
            "auditor_session_id", "auditor_run_id", "audited_commit", "audited_tree_hash",
        ):
            if audit.get(field) != assignment.get(field):
                raise PermissionViolation("architecture audit does not match trusted dispatch", field=field)
    result = {
        "audit_id": audit["audit_id"],
        "gate_id": gate_id,
        "verdict": verdict,
    }
    if store is None:
        return result
    artifact_id = str(audit["audit_id"])
    event = make_event(
        event_type="architecture_audit_ingested",
        reason_code="independent_audit",
        actor_role="control_plane",
        phase_id=gate_id.removesuffix("-GATE"),
        repository_commits=[str(audit["audited_commit"])],
        artifact_refs=[artifact_id],
    )

    def mutation(connection: sqlite3.Connection) -> dict[str, Any]:
        connection.execute(
            "INSERT INTO artifacts(artifact_id, artifact_type, content_hash, path, "
            "integrable, payload_json) VALUES (?, 'architecture-audit', ?, ?, ?, ?)",
            (
                artifact_id,
                sha256_of_json(audit),
                source_path,
                1 if audit["verdict"] == "no_blocking_findings" else 0,
                json.dumps(dict(audit), sort_keys=True, separators=(",", ":")),
            ),
        )
        return result

    return dict(store.execute(f"audit-ingest:{artifact_id}", event, mutation))
