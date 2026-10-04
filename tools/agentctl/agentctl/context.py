"""Minimal, reproducible context-pack compilation and stale-context rebuilds."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from .config import repo_root as resolve_repo_root
from .errors import ContractStale, PlanningError
from .events import make_event
from .hashing import sha256_of_file, sha256_of_json_excluding
from .store import EventStore

__all__ = [
    "CONTEXT_INPUTS",
    "EXCLUDED_BY_POLICY",
    "ContextCompiler",
    "assert_context_hash",
]


CONTEXT_INPUTS: tuple[str, ...] = (
    "authority_excerpt",
    "role_contract",
    "task_contract",
    "decisions",
    "consumed_interfaces",
    "dependency_manifests",
    "failure_lessons",
    "base_commit_summary",
    "permissions",
    "budgets_and_output_schema",
)

EXCLUDED_BY_POLICY: tuple[str, ...] = (
    "prd",
    "conversation_history",
    "research_documents",
    "other_worker_reasoning",
    "secrets",
    "customer_documents",
    "obsolete_plans",
    "raw_logs",
)

_LIST_INPUTS = frozenset(
    {"decisions", "consumed_interfaces", "dependency_manifests", "failure_lessons"}
)

_ROLE_FILES = {
    "architectural_authority": "architect.md",
    "work_package_planning": "planner.md",
    "complex_implementation": "complex-implementer.md",
    "standard_implementation": "standard-implementer.md",
    "mechanical_execution": "mechanical-executor.md",
    "verifier": "verifier.md",
    "integrator": "integrator.md",
}


def _timestamp() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


class ContextCompiler:
    def __init__(
        self,
        repository_root: str | Path | None = None,
        store: EventStore | None = None,
    ) -> None:
        self.root = (
            Path(repository_root).expanduser().resolve()
            if repository_root is not None
            else resolve_repo_root()
        )
        self.store = store
        boundary = json.loads(
            (self.root / "agent-system/policies/product-data-boundary.json").read_text(
                encoding="utf-8"
            )
        )
        enforcement = boundary["enforcement"]
        self.allowed_context_roots = tuple(enforcement["allowed_context_roots"])
        self.excluded_context_paths = tuple(enforcement["excluded_by_default"])

    @staticmethod
    def _path_matches(relative_path: str, rule: str) -> bool:
        if any(character in rule for character in "*?["):
            return PurePosixPath(relative_path).match(rule)
        return relative_path == rule or relative_path.startswith(f"{rule}/")

    def _entry(self, relative_path: str) -> dict[str, Any]:
        if not isinstance(relative_path, str) or not relative_path:
            raise PlanningError("context input path must be a nonempty string")
        candidate = (self.root / relative_path).resolve()
        try:
            normalized = candidate.relative_to(self.root).as_posix()
        except ValueError as exc:
            raise PlanningError("context input escapes the repository", path=relative_path) from exc
        if normalized != relative_path or not candidate.is_file():
            raise PlanningError("context input is missing or unnormalised", path=relative_path)
        if any(
            self._path_matches(normalized, rule) for rule in self.excluded_context_paths
        ):
            raise PlanningError(
                "context input is excluded by the product-data boundary", path=relative_path
            )
        if not any(
            self._path_matches(normalized, rule) for rule in self.allowed_context_roots
        ):
            raise PlanningError(
                "context input is outside the allowed context roots", path=relative_path
            )
        return {
            "path": relative_path,
            "sha256": sha256_of_file(candidate),
            "bytes": candidate.stat().st_size,
        }

    def compile(
        self,
        *,
        task_id: str,
        attempt_id: str,
        role: str,
        inputs: Mapping[str, object],
        compiled_at: str | None = None,
    ) -> dict[str, Any]:
        if tuple(inputs) != CONTEXT_INPUTS:
            raise PlanningError(
                "context inputs must be the exact ordered ten-field set",
                expected=CONTEXT_INPUTS,
                actual=tuple(inputs),
            )
        compiled_inputs: dict[str, Any] = {}
        for name in CONTEXT_INPUTS:
            value = inputs[name]
            if name in _LIST_INPUTS:
                if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
                    raise PlanningError("context list input must be a sequence", input=name)
                compiled_inputs[name] = [self._entry(str(path)) for path in value]
            else:
                if not isinstance(value, str):
                    raise PlanningError("context scalar input must be a path", input=name)
                compiled_inputs[name] = self._entry(value)
        manifest: dict[str, Any] = {
            "artifact_type": "context-manifest",
            "context_hash": "",
            "task_id": task_id,
            "attempt_id": attempt_id,
            "compiled_at": compiled_at or _timestamp(),
            "role": role,
            "inputs": compiled_inputs,
            "excluded_by_policy": list(EXCLUDED_BY_POLICY),
        }
        manifest["context_hash"] = sha256_of_json_excluding(manifest, "context_hash")
        return manifest

    def default_inputs(self, task_id: str, role: str) -> dict[str, object]:
        contract_path, contract = self._find_task_contract(task_id)
        decisions: list[str] = []
        for decision_id in contract.get("intent", {}).get("relevant_decisions", []):
            matches = sorted((self.root / "docs" / "decisions").glob(f"{decision_id}-*.md"))
            if matches:
                decisions.append(matches[0].relative_to(self.root).as_posix())
        consumes = contract.get("dependencies", {}).get("consumes", [])
        interface_paths = ["agent-system/policies/semantic-locks.json"] if consumes else []
        base_summary = "agent-system/work/P0/evidence/migration.json"
        if not (self.root / base_summary).is_file():
            base_summary = contract_path
        role_file = _ROLE_FILES.get(role)
        if role_file is None:
            raise PlanningError("unknown role for context compilation", role=role)
        return {
            "authority_excerpt": "AGENTS.md",
            "role_contract": f"agent-system/roles/{role_file}",
            "task_contract": contract_path,
            "decisions": decisions,
            "consumed_interfaces": interface_paths,
            "dependency_manifests": [],
            "failure_lessons": [],
            "base_commit_summary": base_summary,
            "permissions": "agent-system/policies/permissions.json",
            "budgets_and_output_schema": "agent-system/schemas/implementation-manifest.schema.json",
        }

    def rebuild(
        self,
        task_id: str,
        *,
        role: str | None = None,
        inputs: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        if self.store is None:
            raise PlanningError("context rebuild requires an event store")
        rows = self.store.rows(
            "SELECT state, payload_json, context_hash FROM tasks WHERE task_id = ?", (task_id,)
        )
        if not rows:
            raise PlanningError("unknown task", task_id=task_id)
        row = rows[0]
        if row["state"] != "stale":
            raise ContractStale("only a stale task context can be rebuilt", task_id=task_id, state=row["state"])
        task_payload = json.loads(row["payload_json"])
        selected_role = role or task_payload["capability_class"]
        selected_inputs = inputs or self.default_inputs(task_id, selected_role)
        latest_attempt = self.store.scalar(
            "SELECT attempt_id FROM attempts WHERE task_id = ? ORDER BY attempt_number DESC LIMIT 1",
            (task_id,),
        )
        attempt_id = str(latest_attempt or f"{task_id}-context-{uuid.uuid4()}")
        manifest = self.compile(
            task_id=task_id,
            attempt_id=attempt_id,
            role=selected_role,
            inputs=selected_inputs,
        )
        new_hash = manifest["context_hash"]
        if new_hash == row["context_hash"]:
            # A rebuild must demonstrate a new compiled context, not relabel the old one.
            manifest = self.compile(
                task_id=task_id,
                attempt_id=attempt_id,
                role=selected_role,
                inputs=selected_inputs,
                compiled_at=_timestamp(),
            )
            new_hash = manifest["context_hash"]
        artifact_id = f"context:{task_id}:{new_hash}"
        event = make_event(
            event_type="context_rebuilt",
            reason_code="context_rebuilt",
            actor_role="scheduler",
            task_id=task_id,
            attempt_id=attempt_id,
            prior_state="stale",
            next_state="validated",
            context_hash=new_hash,
            artifact_refs=[artifact_id],
        )

        def mutation(connection: sqlite3.Connection) -> dict[str, Any]:
            current = connection.execute(
                "SELECT state, payload_json FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if current is None or current["state"] != "stale":
                raise ContractStale("task changed before context rebuild", task_id=task_id)
            payload = json.loads(current["payload_json"])
            payload["context_hash"] = new_hash
            connection.execute(
                "UPDATE tasks SET state = 'validated', context_hash = ?, payload_json = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE task_id = ?",
                (new_hash, json.dumps(payload, sort_keys=True, separators=(",", ":")), task_id),
            )
            connection.execute(
                "INSERT INTO artifacts(artifact_id, artifact_type, task_id, attempt_id, "
                "content_hash, path, integrable, payload_json) "
                "VALUES (?, 'context-manifest', ?, ?, ?, '', 1, ?)",
                (
                    artifact_id,
                    task_id,
                    attempt_id,
                    new_hash,
                    json.dumps(manifest, sort_keys=True, separators=(",", ":")),
                ),
            )
            return manifest

        return dict(
            self.store.execute(f"context-rebuild:{task_id}:{new_hash}", event, mutation)
        )

    def _find_task_contract(self, task_id: str) -> tuple[str, dict[str, Any]]:
        for path in sorted((self.root / "agent-system").glob("**/*.json")):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                isinstance(document, dict)
                and document.get("artifact_type") == "task-contract"
                and document.get("identity", {}).get("task_id") == task_id
            ):
                return path.relative_to(self.root).as_posix(), document
        raise PlanningError("task contract file was not found", task_id=task_id)


def assert_context_hash(expected: str, artifact: Mapping[str, Any]) -> None:
    """Reject output returned against any context other than the dispatched one."""
    if artifact.get("context_hash") != expected:
        raise ContractStale(
            "artifact context hash does not match the dispatched context",
            expected=expected,
            actual=artifact.get("context_hash"),
        )
