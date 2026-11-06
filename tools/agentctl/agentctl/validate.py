"""Stateless validation for control-plane artifacts.

Validation is deliberately ordered. Schema and local semantic failures are
reported before reference and hash failures so a must-fail fixture is rejected
for the defect it was designed to demonstrate, not for a later consequence.
No candidate-controlled command is run here.
"""

from __future__ import annotations

import copy
import json
import posixpath
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from .config import repo_root as resolve_repo_root
from .errors import InvalidArtifact, SchemaUndeterminable
from .hashing import sha256_of_file, sha256_of_json, sha256_of_json_excluding
from .jsonschema_subset import validate as validate_schema

__all__ = ["ArtifactValidationError", "validate_artifact"]


class ArtifactValidationError(InvalidArtifact):
    """An artifact failure with a stable, instance-specific error code."""

    def __init__(self, code: str, message: str, **context: object) -> None:
        super().__init__(message, **context)
        self.code = code


def _fail(code: str, message: str, **context: object) -> None:
    raise ArtifactValidationError(code, message, **context)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        _fail("artifact_unreadable", "artifact cannot be read", path=str(path), error=str(exc))
    except json.JSONDecodeError as exc:
        _fail(
            "invalid_json",
            "artifact is not valid JSON",
            path=str(path),
            line=exc.lineno,
            column=exc.colno,
        )
    if not isinstance(document, dict):
        raise SchemaUndeterminable(
            "SchemaUndeterminable: artifact root must be an object", path=str(path)
        )
    return document


def _schema_for(document: Mapping[str, Any], root: Path) -> tuple[str, dict[str, Any]]:
    artifact_type = document.get("artifact_type")
    if not isinstance(artifact_type, str) or not artifact_type:
        raise SchemaUndeterminable(
            "SchemaUndeterminable: artifact_type is missing or is not a nonempty string"
        )

    schema_path: Path | None = None
    schema: dict[str, Any] | None = None
    for candidate in sorted((root / "agent-system" / "schemas").glob("*.schema.json")):
        candidate_schema = _read_json(candidate)
        declared = candidate_schema.get("properties", {}).get("artifact_type", {}).get("const")
        if declared == artifact_type:
            schema_path = candidate
            schema = candidate_schema
            break
    if schema_path is None or schema is None:
        raise SchemaUndeterminable(
            f"SchemaUndeterminable: no schema for artifact_type {artifact_type!r}",
            artifact_type=artifact_type,
        )
    declared = schema.get("properties", {}).get("artifact_type", {}).get("const")
    if declared != artifact_type:
        raise SchemaUndeterminable(
            "SchemaUndeterminable: schema discriminator does not match its artifact type",
            artifact_type=artifact_type,
            schema=str(schema_path),
        )
    return artifact_type, schema


def _schema_failure_code(errors: list[tuple[str, str, str]]) -> str:
    for pointer, keyword, message in errors:
        if keyword == "required" and "'escalation'" in message:
            return "missing_required_field"
        if pointer == "/permissions/secret_access" and keyword == "const":
            return "secret_access_true"
        if pointer == "/budgets/maximum_attempts" and keyword == "maximum":
            return "attempts_over_ceiling"
        if pointer == "/candidate_patch_unchanged" and keyword == "const":
            return "patch_altered"
        if keyword == "pattern" and ("path" in pointer or "/scope/" in pointer):
            return "path_traversal"
    return "schema_validation_failed"


def _validate_schema(document: Mapping[str, Any], schema: Mapping[str, Any]) -> None:
    ok, errors = validate_schema(document, schema)
    if ok:
        return
    code = _schema_failure_code(errors)
    pointer, keyword, message = errors[0]
    _fail(
        code,
        "artifact does not conform to its schema",
        json_pointer=pointer,
        keyword=keyword,
        detail=message,
        errors=errors,
    )


def _iter_path_values(value: Any, key: str | None = None) -> Iterator[tuple[str, str]]:
    """Yield path-like values without treating evidence URLs as filesystem paths."""
    if isinstance(value, dict):
        for child_key, child in value.items():
            if child_key == "path" and isinstance(child, str):
                yield child_key, child
            elif child_key.endswith("_paths") and isinstance(child, list):
                for item in child:
                    if isinstance(item, str):
                        yield child_key, item
            else:
                yield from _iter_path_values(child, child_key)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_path_values(child, key)


def _path_is_safe(value: str, root: Path) -> bool:
    if not value or value == "**" or value.startswith("/") or "\\" in value:
        return False
    segments = value.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        return False
    if posixpath.normpath(value) != value:
        return False

    # Lexical checks above handle globs. Concrete paths also get a real-path
    # boundary check so an existing symlink cannot escape the repository.
    if not any(character in value for character in "*?["):
        resolved = (root / value).resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError:
            return False
    return True


def _validate_paths(document: Mapping[str, Any], root: Path) -> None:
    for field, value in _iter_path_values(document):
        if not _path_is_safe(value, root):
            _fail(
                "path_traversal",
                "path is broad, absolute, unnormalised, or outside the repository",
                field=field,
                path=value,
            )

    if document.get("artifact_type") == "task-contract":
        scope = document["scope"]
        overlap = sorted(set(scope["writable_paths"]) & set(scope["prohibited_paths"]))
        if overlap:
            _fail(
                "overlapping_write_prohibit",
                "writable_paths and prohibited_paths must be disjoint",
                overlap=overlap,
            )


def _validate_local_semantics(artifact_type: str, document: Mapping[str, Any], root: Path) -> None:
    _validate_paths(document, root)

    if artifact_type == "integration-manifest" and not document["candidate_patch_unchanged"]:
        _fail("patch_altered", "integration manifest reports an altered candidate patch")

    if artifact_type == "context-manifest":
        from .context import EXCLUDED_BY_POLICY

        exclusions = document["excluded_by_policy"]
        if tuple(exclusions) != EXCLUDED_BY_POLICY:
            _fail(
                "context_exclusions_invalid",
                "context manifest must record the exact ordered exclusion policy",
                expected=EXCLUDED_BY_POLICY,
                actual=tuple(exclusions),
            )

    if artifact_type != "task-contract":
        return

    identity = document["identity"]
    routing = document["routing"]
    permissions = document["permissions"]
    budgets = document["budgets"]
    verification = document["verification"]

    policy = json.loads(
        (root / "agent-system/policies/semantic-locks.json").read_text(encoding="utf-8")
    )
    registered_locks = {str(entry["lock_id"]) for entry in policy["entries"]}
    unknown_locks = sorted(set(document["scope"]["semantic_locks"]) - registered_locks)
    if unknown_locks:
        _fail(
            "semantic_lock_unregistered",
            "task contract requests an unregistered semantic lock",
            lock_ids=unknown_locks,
        )

    if identity["base_commit"] == "unbound" and identity["status"] != "proposed":
        _fail("unbound_base_commit", "base_commit may be unbound only while status is proposed")
    if routing["risk"] == "critical" and permissions["enforcement_class"] == "unsandboxed_tools":
        _fail("critical_unsandboxed", "critical tasks cannot use unsandboxed_tools")

    approval_requirements: list[str] = []
    if permissions["destructive_commands"]:
        approval_requirements.append("destructive_operation")
    if permissions.get("required_human_approval_action") == "consequential_adr_acceptance":
        approval_requirements.append("consequential_adr_acceptance")
    if permissions["may_modify_tests"] == "protected_with_approval":
        approval_requirements.append("protected_test_change")
    if approval_requirements:
        approval_ref = permissions.get("human_approval_ref")
        if not isinstance(approval_ref, str) or not approval_ref.strip():
            _fail(
                "human_approval_required",
                "task permission requires a bound human approval reference",
                required_actions=approval_requirements,
            )
        actual_action = permissions.get("required_human_approval_action")
        if len(approval_requirements) != 1 or actual_action != approval_requirements[0]:
            _fail(
                "human_approval_wrong_action",
                "task permission is not bound to its required approval action",
                required_actions=approval_requirements,
                actual=actual_action,
            )

    floors = {
        "maximum_attempts": 1,
        "maximum_repair_cycles": 0,
        "maximum_artifact_corrections": 0,
    }
    for field, floor in floors.items():
        if budgets[field] < floor:
            _fail(
                "budget_below_floor",
                "task budget is below its permitted floor",
                field=field,
                value=budgets[field],
                minimum=floor,
            )

    required_evidence = (
        "deterministic_checks",
        "behavioral_requirements",
        "expected_artifacts",
        "definition_of_done",
    )
    for field in required_evidence:
        if not verification[field]:
            _fail(
                "acceptance_evidence_missing",
                "task contract has no required acceptance evidence",
                field=field,
            )
    if not document["intent"]["acceptance_behavior"]:
        _fail(
            "acceptance_evidence_missing",
            "task contract has no acceptance behavior",
            field="acceptance_behavior",
        )


def _walk_items(value: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk_items(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_items(child)


def _validate_ids_and_versions(document: Mapping[str, Any]) -> None:
    for key, value in _walk_items(document):
        if key.endswith("_id") and (not isinstance(value, str) or not value.strip()):
            _fail("invalid_identifier", "identifier must be a nonempty string", field=key)

    version_fields: list[tuple[str, Any]] = []
    artifact_type = document["artifact_type"]
    if artifact_type == "task-contract":
        version_fields.append(("contract_version", document["identity"]["contract_version"]))
    if artifact_type in ("work-package", "human-approval"):
        version_fields.append(("schema_version", document["schema_version"]))
    for field, value in version_fields:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            _fail(
                "invalid_contract_version",
                "contract and schema versions must be positive integers",
                field=field,
                value=value,
            )


def _candidate_artifact_paths(root: Path, source: Path) -> Iterator[Path]:
    seen: set[Path] = set()
    roots = [
        source.parent,
        root / "agent-system" / "examples" / "valid",
        root / "agent-system" / "work",
    ]
    for candidate_root in roots:
        if not candidate_root.is_dir():
            continue
        for path in sorted(candidate_root.rglob("*.json")):
            resolved = path.resolve()
            if resolved in seen or "invalid" in path.parts:
                continue
            seen.add(resolved)
            yield path


def _artifact_index(root: Path, source: Path) -> list[dict[str, Any]]:
    indexed: list[dict[str, Any]] = []
    for path in _candidate_artifact_paths(root, source):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(document, dict) and isinstance(document.get("artifact_type"), str):
            indexed.append(document)
    return indexed


def _find_artifact(
    index: Iterable[Mapping[str, Any]], artifact_type: str, *, identifier: str, field: str
) -> Mapping[str, Any] | None:
    for candidate in index:
        if candidate.get("artifact_type") != artifact_type:
            continue
        if artifact_type == "task-contract":
            value = candidate.get("identity", {}).get(field)
        else:
            value = candidate.get(field)
        if value == identifier:
            return candidate
    return None


def _validate_dependencies(
    artifact_type: str, document: Mapping[str, Any], root: Path, source: Path
) -> None:
    if artifact_type not in ("task-contract", "work-package"):
        return
    index = _artifact_index(root, source)
    if artifact_type == "task-contract":
        work_package_id = document["identity"]["work_package_id"]
        if _find_artifact(
            index, "work-package", identifier=work_package_id, field="work_package_id"
        ) is None:
            _fail(
                "dependency_unresolved",
                "task contract references an unknown work package",
                work_package_id=work_package_id,
            )
        for task_id in document["dependencies"]["requires"]:
            if _find_artifact(index, "task-contract", identifier=task_id, field="task_id") is None:
                _fail(
                    "dependency_unresolved",
                    "task dependency does not resolve",
                    task_id=task_id,
                )
    else:
        for work_package_id in document["depends_on"]:
            if _find_artifact(
                index, "work-package", identifier=work_package_id, field="work_package_id"
            ) is None:
                if document.get("bootstrap_phase") is True and document.get("status") == "complete":
                    program_dag_path = root / "agent-system/work/program-dag.json"
                    if program_dag_path.is_file():
                        program_dag = json.loads(program_dag_path.read_text(encoding="utf-8"))
                        recorded = {
                            str(item.get("work_package_id"))
                            for item in program_dag.get("work_packages", ())
                            if item.get("status") == "complete"
                        }
                        if work_package_id in recorded:
                            continue
                _fail(
                    "dependency_unresolved",
                    "work-package dependency does not resolve",
                    work_package_id=work_package_id,
                )


def _task_contract_hash(document: Mapping[str, Any]) -> str:
    unhashed = copy.deepcopy(document)
    unhashed["identity"].pop("contract_hash", None)
    return sha256_of_json(unhashed)


def _validate_context_inputs(document: Mapping[str, Any], root: Path) -> None:
    for _key, value in _walk_items(document["inputs"]):
        if not isinstance(value, dict) or not {"path", "sha256", "bytes"}.issubset(value):
            continue
        path = root / value["path"]
        if not path.is_file():
            _fail("artifact_hash_mismatch", "context input does not exist", path=value["path"])
        actual_bytes = path.stat().st_size
        actual_hash = sha256_of_file(path)
        if actual_bytes != value["bytes"] or actual_hash != value["sha256"]:
            _fail(
                "artifact_hash_mismatch",
                "context input hash or byte count does not match",
                path=value["path"],
                expected_hash=value["sha256"],
                actual_hash=actual_hash,
                expected_bytes=value["bytes"],
                actual_bytes=actual_bytes,
            )


def _validate_hashes(artifact_type: str, document: Mapping[str, Any], root: Path) -> None:
    if artifact_type == "task-contract":
        expected = document["identity"]["contract_hash"]
        actual = _task_contract_hash(document)
        if actual != expected:
            _fail(
                "artifact_hash_mismatch",
                "task contract self-hash does not match",
                expected=expected,
                actual=actual,
            )
    elif artifact_type == "context-manifest":
        expected = document["context_hash"]
        actual = sha256_of_json_excluding(document, "context_hash")
        if actual != expected:
            _fail(
                "artifact_hash_mismatch",
                "context manifest self-hash does not match",
                expected=expected,
                actual=actual,
            )
        _validate_context_inputs(document, root)


def _find_context(
    index: Iterable[Mapping[str, Any]], task_id: str, context_hash: str
) -> Mapping[str, Any] | None:
    task_context: Mapping[str, Any] | None = None
    for candidate in index:
        if candidate.get("artifact_type") != "context-manifest":
            continue
        if candidate.get("task_id") != task_id:
            continue
        task_context = task_context or candidate
        if candidate.get("context_hash") == context_hash:
            return candidate
    # Returning another context for the task lets the caller report the more
    # precise hash-link violation instead of misclassifying tampering as a
    # missing dependency.
    return task_context


def _require_linked(
    value: Mapping[str, Any] | None, *, description: str, **context: object
) -> Mapping[str, Any]:
    if value is None:
        _fail("dependency_unresolved", f"referenced {description} does not resolve", **context)
    return value


def _assert_link(actual: Any, expected: Any, field: str) -> None:
    if actual != expected:
        _fail(
            "artifact_hash_mismatch",
            "cross-artifact hash or identity link does not match",
            field=field,
            expected=expected,
            actual=actual,
        )


def _validate_cross_artifact_links(
    artifact_type: str, document: Mapping[str, Any], root: Path, source: Path
) -> None:
    if artifact_type not in {
        "implementation-manifest",
        "review-verdict",
        "integration-manifest",
        "phase-gate",
        "human-approval",
    }:
        return
    index = _artifact_index(root, source)

    if artifact_type in ("implementation-manifest", "review-verdict"):
        task_id = document["task_id"]
        attempt_id = document["attempt_id"]
        task = _require_linked(
            _find_artifact(index, "task-contract", identifier=task_id, field="task_id"),
            description="task contract",
            task_id=task_id,
        )
        context = _require_linked(
            _find_context(index, task_id, document["context_hash"]),
            description="context manifest",
            task_id=task_id,
            context_hash=document["context_hash"],
        )
        _assert_link(
            document["contract_hash"], task["identity"]["contract_hash"], "contract_hash"
        )
        _assert_link(document["context_hash"], context["context_hash"], "context_hash")

        if artifact_type == "review-verdict":
            implementation = next(
                (
                    candidate
                    for candidate in index
                    if candidate.get("artifact_type") == "implementation-manifest"
                    and candidate.get("task_id") == task_id
                    and candidate.get("attempt_id") == attempt_id
                ),
                None,
            )
            implementation = _require_linked(
                implementation,
                description="implementation manifest",
                task_id=task_id,
                attempt_id=attempt_id,
            )
            _assert_link(
                document["reviewed_result_commit"],
                implementation["result_commit"],
                "reviewed_result_commit",
            )
        return

    if artifact_type == "integration-manifest":
        review = _require_linked(
            _find_artifact(
                index,
                "review-verdict",
                identifier=document["review_id"],
                field="review_id",
            ),
            description="review verdict",
            review_id=document["review_id"],
        )
        _assert_link(document["task_id"], review["task_id"], "task_id")
        _assert_link(
            document["candidate_result_commit"],
            review["reviewed_result_commit"],
            "candidate_result_commit",
        )
        _assert_link(document["reviewed_diff_hash"], review["diff_hash"], "reviewed_diff_hash")
        return

    if artifact_type == "phase-gate":
        for criterion in document["criteria"]:
            evidence_ref = criterion.get("evidence_ref")
            evidence_hash = criterion.get("evidence_hash")
            if evidence_ref is None or evidence_hash is None:
                continue
            if not _path_is_safe(evidence_ref, root):
                _fail("path_traversal", "gate evidence path is unsafe", path=evidence_ref)
            evidence_path = root / evidence_ref
            if not evidence_path.is_file():
                _fail(
                    "dependency_unresolved",
                    "gate evidence reference does not resolve",
                    path=evidence_ref,
                )
            try:
                evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                actual_hash = sha256_of_file(evidence_path)
            else:
                actual_hash = sha256_of_json(evidence)
            _assert_link(evidence_hash, actual_hash, "evidence_hash")
        return

    target_type = document["target_type"]
    target_id = document["target_id"]
    if target_type == "adr":
        if target_id != "ADR-0005":
            _fail(
                "dependency_unresolved",
                "referenced approval target does not resolve",
                target_type=target_type,
                target_id=target_id,
            )
        adr_path = root / "docs/decisions/ADR-0005-symmetric-approval-seal.md"
        if not adr_path.is_file() or not _path_is_safe(str(adr_path.relative_to(root)), root):
            _fail("dependency_unresolved", "referenced approval target does not resolve", target_id=target_id)
        text = adr_path.read_text(encoding="utf-8")
        if not text.startswith("# ADR-0005 ") or "\n## Status\n\nstatus: proposed\n" not in text:
            _fail("dependency_unresolved", "ADR approval target is not currently proposed", target_id=target_id)
        _assert_link(document["target_hash"], sha256_of_file(adr_path), "target_hash")
        return
    id_fields = {
        "phase-gate": "gate_id",
        "task-contract": "task_id",
        "work-package": "work_package_id",
        "architecture-audit": "audit_id",
    }
    id_field = id_fields.get(target_type)
    target = (
        _find_artifact(index, target_type, identifier=target_id, field=id_field)
        if id_field is not None
        else None
    )
    target = _require_linked(
        target,
        description="approval target",
        target_type=target_type,
        target_id=target_id,
    )
    _assert_link(document["target_hash"], sha256_of_json(target), "target_hash")


def validate_artifact(
    path: str | Path, *, repository_root: str | Path | None = None
) -> dict[str, Any]:
    """Validate one JSON artifact and return its decoded mapping.

    The function is stateless: it reads schemas and referenced committed
    artifacts, but never writes control-plane state.
    """
    source = Path(path).expanduser().resolve()
    root = (
        Path(repository_root).expanduser().resolve()
        if repository_root
        else resolve_repo_root()
    )
    document = _read_json(source)
    artifact_type, schema = _schema_for(document, root)
    _validate_schema(document, schema)
    _validate_local_semantics(artifact_type, document, root)
    _validate_ids_and_versions(document)
    _validate_dependencies(artifact_type, document, root, source)
    _validate_hashes(artifact_type, document, root)
    _validate_cross_artifact_links(artifact_type, document, root, source)
    return document
