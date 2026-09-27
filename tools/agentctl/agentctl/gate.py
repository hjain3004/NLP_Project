"""Phase-gate evidence re-execution and architecture-audit ingestion."""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import sqlite3
import subprocess
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .approval import ApprovalManager, ApprovalRejected, verify_approval
from .config import load as load_config
from .errors import InvalidArtifact, PermissionViolation, PlanningError
from .events import make_event
from .hashing import sha256_hex, sha256_of_file, sha256_of_json
from .store import EventStore
from .validate import validate_artifact

__all__ = ["GateEvaluator", "ingest_audit"]


def _timestamp() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


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
                "expected_target_hash": sha256_of_file(criteria_path),
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
                "expected_target_hash": sha256_of_file(statement),
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
                    completed = subprocess.run(
                        command,
                        cwd=self.root,
                        shell=True,
                        executable="/bin/sh",
                        capture_output=True,
                        timeout=self.command_timeout_seconds,
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
        selected_criteria = [
            item
            for item in source.get("criteria", [])
            if evidence_types is None or item.get("evidence_type") in evidence_types
        ]
        criteria = [
            self._evaluate_criterion(
                item,
                gate_id=str(source.get("gate_id", "")),
                criteria_path=source_path,
            )
            for item in selected_criteria
        ]
        if not criteria:
            raise PlanningError("phase gate has no criteria", path=str(source_path))
        results = {item["result"] for item in criteria}
        gate_result = "failed" if "fail" in results else "open" if "open" in results else "passed"
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        head = commit.stdout.strip() if commit.returncode == 0 else "0" * 40
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
        if gate_result == "passed" and self.store is not None:
            approval_requests: list[dict[str, str]] = []
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
                    {
                        "approval_id": str(record["approval_id"]),
                        **expectations,
                    }
                )
            if approval_requests:
                manager = ApprovalManager(
                    self.store,
                    key=self.approval_key,
                )
                manager.consume_many(tuple(approval_requests))
        if result_path is None:
            name = source_path.name.replace("-criteria.json", "-result.json")
            if name == source_path.name:
                name = f"{source['gate_id']}-result.json"
            destination = source_path.with_name(name)
        else:
            destination = Path(result_path)
            if not destination.is_absolute():
                destination = self._path(destination.as_posix())
        if write_result:
            destination.write_text(
                json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8"
            )
        if self.store is not None:
            self._record(result, destination)
        return result

    def _record(self, result: Mapping[str, Any], destination: Path) -> None:
        assert self.store is not None
        artifact_id = f"gate:{result['gate_id']}:{uuid.uuid4()}"
        event = make_event(
            event_type="phase_gate_evaluated",
            reason_code=f"gate_{result['gate_result']}",
            actor_role="control_plane",
            phase_id=result["phase_id"],
            repository_commits=[result["evaluated_against_commit"]],
            artifact_refs=[artifact_id],
        )

        def mutation(connection: sqlite3.Connection) -> dict[str, Any]:
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

        self.store.execute(f"gate-evaluate:{artifact_id}", event, mutation)


def ingest_audit(
    gate_id: str,
    audit: Mapping[str, Any],
    *,
    construction_log: str | os.PathLike[str],
    store: EventStore | None = None,
    source_path: str = "",
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
    result = {
        "audit_id": audit["audit_id"],
        "gate_id": gate_id,
        "verdict": audit["verdict"],
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
