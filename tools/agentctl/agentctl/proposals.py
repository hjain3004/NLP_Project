"""Validation for explicitly non-authoritative governance proposals."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .hashing import canonical_json, sha256_of_file, sha256_of_json

__all__ = ["validate_governance_change_set", "validate_g0_criteria_candidate"]


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"proposal root is not an object: {path}")
    return value


def _require_proposal(value: dict[str, Any], artifact_type: str) -> None:
    if value.get("artifact_type") != artifact_type:
        raise ValueError("proposal artifact type is incorrect")
    if value.get("status") != "proposed" or value.get("authoritative") is not False:
        raise ValueError("proposal is not explicitly non-authoritative")


def _validate_json_operation(operation: dict[str, Any], root: Path) -> None:
    target = root / str(operation["path"])
    if sha256_of_file(target) != operation["before_bytes_sha256"]:
        raise ValueError(f"canonical bytes changed before proposal approval: {operation['path']}")
    before_document = _read(target)
    after_document = operation.get("after_document")
    if after_document is None:
        after_document = before_document
        for patch in operation.get("patches", []):
            pointer = str(patch["pointer"])
            parts = [part for part in pointer.lstrip("/").split("/") if part]
            parent: Any = after_document
            for part in parts[:-1]:
                parent = parent[int(part)] if isinstance(parent, list) else parent[part]
            leaf = parts[-1]
            if patch["op"] == "append":
                parent[leaf].append(patch["value"])
            elif patch["op"] == "set":
                parent[leaf] = patch["value"]
            else:
                raise ValueError(f"unsupported JSON proposal operation: {patch['op']}")
    if not isinstance(after_document, dict):
        raise ValueError(f"proposal lacks exact replacement JSON: {operation['path']}")
    after_bytes = canonical_json(after_document).encode("utf-8")
    if hashlib.sha256(after_bytes).hexdigest() != operation["after_canonical_json_sha256"]:
        raise ValueError(f"proposal replacement hash mismatch: {operation['path']}")


def validate_governance_change_set(
    source: str | Path, *, repository_root: str | Path
) -> dict[str, Any]:
    root = Path(repository_root).resolve()
    proposal = _read(Path(source))
    _require_proposal(proposal, "governance-change-set-proposal")
    operations = proposal.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError("governance proposal has no operations")
    paths = {str(item.get("path")) for item in operations if isinstance(item, dict)}
    required = {
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
    if not required <= paths:
        raise ValueError(f"governance proposal omits required paths: {sorted(required - paths)}")
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError("governance operation is not an object")
        if operation.get("encoding") == "canonical_json_utf8":
            _validate_json_operation(operation, root)
        elif operation.get("encoding") == "utf8_patch":
            target = root / str(operation["path"])
            before = target.read_bytes()
            if hashlib.sha256(before).hexdigest() != operation["before_bytes_sha256"]:
                raise ValueError(f"canonical bytes changed before proposal approval: {target}")
            old = str(operation["replace"]).encode("utf-8")
            new = str(operation["with"]).encode("utf-8")
            if before.count(old) != 1:
                raise ValueError(f"proposal patch is not uniquely applicable: {target}")
            if hashlib.sha256(before.replace(old, new, 1)).hexdigest() != operation["after_bytes_sha256"]:
                raise ValueError(f"proposal patch hash mismatch: {target}")
        else:
            raise ValueError(f"unsupported proposal encoding: {operation.get('encoding')}")
    def after(path: str) -> dict[str, Any]:
        op = next(op for op in operations if op["path"] == path)
        document = _read(root / path)
        for patch in op.get("patches", []):
            pointer = str(patch["pointer"])
            parts = [part for part in pointer.lstrip("/").split("/") if part]
            parent: Any = document
            for part in parts[:-1]:
                parent = parent[int(part)] if isinstance(parent, list) else parent[part]
            leaf = parts[-1]
            if patch["op"] == "append":
                parent[leaf].append(patch["value"])
            else:
                parent[leaf] = patch["value"]
        return document

    action = after("agent-system/schemas/human-approval.schema.json")
    if "consequential_adr_acceptance" not in action["properties"]["authorized_action"]["enum"]:
        raise ValueError("proposal does not add consequential_adr_acceptance to schema")
    policy = after("agent-system/policies/approval-policy.json")
    if policy["tiers"].get("consequential_adr_acceptance") != ["keychain_backed", "local_authentication"]:
        raise ValueError("consequential ADR approval does not require a strong tier")
    if not any("consequential_adr_acceptance" in str(op.get("consumer", "")) for op in operations):
        raise ValueError("proposal does not identify a consequential ADR consumer")
    return {"status": "ok", "authoritative": False, "operation_count": len(operations)}


def validate_g0_criteria_candidate(
    source: str | Path, *, repository_root: str | Path
) -> dict[str, Any]:
    root = Path(repository_root).resolve()
    candidate_path = Path(source).resolve()
    proposal = _read(candidate_path)
    _require_proposal(proposal, "phase-gate-criteria-proposal")
    if proposal.get("gate_id") != "G0" or proposal.get("phase_id") != "P0":
        raise ValueError("candidate does not bind G0/P0")
    criteria = proposal.get("criteria")
    if not isinstance(criteria, list) or {item.get("criterion_id") for item in criteria} != {
        f"G0-{index:02d}" for index in range(1, 15)
    }:
        raise ValueError("candidate must contain exactly fourteen G0 criteria")
    by_id = {item["criterion_id"]: item for item in criteria}
    nested = by_id["G0-03"]["evidence_command"]
    if "os.walk" not in nested or ".git" not in nested or "ls-files" not in nested:
        raise ValueError("G0-03 does not detect nested Git directories, files, and gitlinks")
    expected_event_hash = proposal["event_export_sha256"]
    for index in range(5, 10):
        item = by_id[f"G0-{index:02d}"]
        command = item["evidence_command"]
        if "agent-system/work/DRY-RUN/evidence/events.json" not in command:
            raise ValueError(f"G0-{index:02d} omits committed event export")
        if "verify_dry_run_semantics" not in command or "require_committed=True" not in command:
            raise ValueError(f"G0-{index:02d} omits semantic replay")
        if item.get("evidence_sha256") != expected_event_hash:
            raise ValueError(f"G0-{index:02d} lacks deterministic evidence hash")
    clean = by_id["G0-11"]["evidence_command"]
    if proposal["exact_head"] not in clean or "git rev-parse HEAD" not in clean or "docs verify" not in clean:
        raise ValueError("G0-11 does not bind clean-checkout exact-HEAD verification")
    canonical = root / "agent-system/work/P0/gates/G0-criteria.json"
    if sha256_of_file(canonical) != proposal["canonical_current_sha256"]:
        raise ValueError("canonical G0 criteria changed or proposal hash is stale")
    if candidate_path == canonical:
        raise ValueError("G0 candidate path is authoritative")
    return {"status": "ok", "authoritative": False, "criterion_count": len(criteria)}
