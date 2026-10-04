"""Deterministic task state machine, admission checks, and artifact ingestion."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

from .approval import ApprovalManager, ApprovalRejected
from .errors import (
    BudgetExhausted,
    ContractStale,
    IllegalTransition,
    PermissionViolation,
    PlanningError,
)
from .events import make_event
from .leases import Lease, LeaseManager
from .locks import path_patterns_overlap, write_sets_conflict
from .models import TERMINAL_STATES, TaskState
from .store import EventStore

__all__ = [
    "ADMISSION_CONDITIONS",
    "SCHEDULER_CYCLE",
    "TRANSITION_RULES",
    "AdmissionDecision",
    "AdmissionFacts",
    "Scheduler",
    "TaskSpec",
    "admission_decision",
    "task_approval_scope",
    "transition",
]


@dataclass(frozen=True)
class TransitionRule:
    current_states: tuple[TaskState, ...] | str
    event: str
    next_states: tuple[TaskState, ...]


_NONTERMINAL = tuple(state for state in TaskState if state not in TERMINAL_STATES)

TRANSITION_RULES: tuple[TransitionRule, ...] = (
    TransitionRule((TaskState.DRAFT,), "schema_policy_passed", (TaskState.VALIDATED,)),
    TransitionRule((TaskState.VALIDATED,), "admission_passed", (TaskState.READY,)),
    TransitionRule((TaskState.READY,), "lease_issued", (TaskState.LEASED,)),
    TransitionRule((TaskState.LEASED,), "worker_acknowledged", (TaskState.RUNNING,)),
    TransitionRule((TaskState.RUNNING,), "schema_correction_requested", (TaskState.RUNNING,)),
    TransitionRule((TaskState.RUNNING,), "submission_failed", (TaskState.REPAIR_REQUIRED, TaskState.ESCALATED)),
    TransitionRule((TaskState.RUNNING,), "candidate_ingested", (TaskState.CANDIDATE,)),
    TransitionRule((TaskState.CANDIDATE,), "verification_started", (TaskState.DETERMINISTIC_VERIFICATION,)),
    TransitionRule((TaskState.DETERMINISTIC_VERIFICATION,), "checks_passed", (TaskState.INDEPENDENT_REVIEW,)),
    TransitionRule((TaskState.DETERMINISTIC_VERIFICATION,), "implementation_defect", (TaskState.REPAIR_REQUIRED, TaskState.ESCALATED)),
    TransitionRule((TaskState.INDEPENDENT_REVIEW,), "review_passed", (TaskState.INTEGRATION_READY,)),
    TransitionRule((TaskState.INDEPENDENT_REVIEW,), "review_blocked", (TaskState.REPAIR_REQUIRED, TaskState.ESCALATED)),
    TransitionRule((TaskState.INTEGRATION_READY,), "integration_lease_issued", (TaskState.INTEGRATING,)),
    TransitionRule((TaskState.INTEGRATING,), "integration_passed", (TaskState.COMPLETED,)),
    TransitionRule("nonterminal", "dependency_changed", (TaskState.STALE,)),
    TransitionRule((TaskState.LEASED, TaskState.RUNNING), "lease_expired", (TaskState.READY,)),
    TransitionRule((), "late_artifact", ()),
    TransitionRule("nonterminal", "dependency_unavailable", (TaskState.BLOCKED,)),
    TransitionRule((TaskState.REPAIR_REQUIRED,), "repair_authorized", (TaskState.READY,)),
    TransitionRule((TaskState.DRAFT, TaskState.VALIDATED), "contract_invalid", (TaskState.REPLAN_REQUIRED,)),
    TransitionRule((TaskState.STALE,), "context_rebuilt", (TaskState.READY,)),
    TransitionRule((TaskState.BLOCKED,), "blocker_resolved", (TaskState.READY,)),
    TransitionRule((TaskState.REPLAN_REQUIRED,), "replacement_plan_accepted", (TaskState.VALIDATED, TaskState.CANCELED)),
    TransitionRule((TaskState.ESCALATED,), "authority_decision", (TaskState.VALIDATED, TaskState.BLOCKED, TaskState.CANCELED)),
)


def transition(
    state: TaskState | str,
    event: str,
    desired: TaskState | str | None = None,
) -> TaskState:
    current = state if isinstance(state, TaskState) else TaskState(state)
    requested = None if desired is None else (desired if isinstance(desired, TaskState) else TaskState(desired))
    for rule in TRANSITION_RULES:
        matches = (
            rule.current_states == "nonterminal" and current in _NONTERMINAL
        ) or (
            isinstance(rule.current_states, tuple) and current in rule.current_states
        )
        if not matches or rule.event != event or not rule.next_states:
            continue
        if requested is not None:
            if requested not in rule.next_states:
                raise IllegalTransition(
                    "requested outcome is not permitted",
                    state=current.value,
                    event=event,
                    desired=requested.value,
                )
            return requested
        return rule.next_states[0]
    raise IllegalTransition("state/event pair is absent", state=current.value, event=event)


ADMISSION_CONDITIONS: tuple[str, ...] = (
    "dependencies_complete",
    "context_current",
    "base_commit_compatible",
    "path_locks_available",
    "semantic_locks_available",
    "capability_sufficient",
    "reviewer_available",
    "budget_available",
    "enforcement_compatible",
    "human_approval_available",
)

_ADMISSION_FAILURES = (
    "dependency_incomplete",
    "context_stale",
    "base_commit_incompatible",
    "path_lock_conflict",
    "semantic_lock_conflict",
    "capability_floor",
    "reviewer_unavailable",
    "budget_exhausted",
    "enforcement_conflict",
    "human_approval_required",
)

SCHEDULER_CYCLE: tuple[str, ...] = (
    "refresh",
    "invalidate_stale_contexts",
    "expire_dead_leases",
    "compute_ready",
    "reject_lock_conflicts",
    "reserve_review_and_integration_capacity",
    "enforce_budgets",
    "issue_leases",
    "checkpoint",
)


@dataclass(frozen=True)
class AdmissionFacts:
    dependencies_complete: bool
    context_current: bool
    base_commit_compatible: bool
    path_locks_available: bool
    semantic_locks_available: bool
    capability_sufficient: bool
    reviewer_available: bool
    budget_available: bool
    enforcement_compatible: bool
    human_approval_available: bool


@dataclass(frozen=True)
class AdmissionDecision:
    allowed: bool
    reason_code: str | None


def admission_decision(facts: AdmissionFacts) -> AdmissionDecision:
    values = tuple(getattr(facts, name) for name in ADMISSION_CONDITIONS)
    for passed, reason_code in zip(values, _ADMISSION_FAILURES):
        if not passed:
            return AdmissionDecision(False, reason_code)
    return AdmissionDecision(True, None)


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    work_package_id: str
    contract_hash: str
    context_hash: str
    base_commit: str
    capability_class: str
    risk: str
    enforcement_class: str
    writable_paths: tuple[str, ...]
    prohibited_paths: tuple[str, ...]
    semantic_locks: Mapping[str, str]
    maximum_attempts: int
    maximum_artifact_corrections: int
    dependencies: tuple[str, ...] = ()
    interface_pins: Mapping[str, str] | None = None
    human_approval_ref: str | None = None
    human_approval_action: str | None = None

    def with_semantic_locks(self, claims: Mapping[str, str]) -> "TaskSpec":
        return replace(self, semantic_locks=dict(claims))

    def to_payload(self) -> dict[str, Any]:
        value = asdict(self)
        value["writable_paths"] = list(self.writable_paths)
        value["prohibited_paths"] = list(self.prohibited_paths)
        value["dependencies"] = list(self.dependencies)
        return value

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> "TaskSpec":
        data = dict(value)
        data["writable_paths"] = tuple(data.get("writable_paths", ()))
        data["prohibited_paths"] = tuple(data.get("prohibited_paths", ()))
        data["dependencies"] = tuple(data.get("dependencies", ()))
        return cls(**data)


_CAPABILITY_RANK = {
    "mechanical_execution": 0,
    "standard_implementation": 1,
    "complex_implementation": 2,
    "work_package_planning": 2,
    "verifier": 2,
    "integrator": 2,
    "architectural_authority": 3,
}


def task_approval_scope(spec: TaskSpec) -> str:
    if not spec.human_approval_action:
        raise PlanningError("task has no required human approval action", task_id=spec.task_id)
    return (
        f"Authorize {spec.human_approval_action} for task-contract "
        f"{spec.task_id}@{spec.contract_hash}"
    )


class Scheduler:
    def __init__(
        self, store: EventStore, *, approval_manager: ApprovalManager | None = None
    ) -> None:
        self.store = store
        self.leases = LeaseManager(store)
        self.approvals = approval_manager

    def _approval_request(self, spec: TaskSpec) -> dict[str, str] | None:
        if spec.human_approval_ref is None:
            return None
        if not spec.human_approval_action:
            raise PlanningError(
                "task approval reference has no required action", task_id=spec.task_id
            )
        return {
            "approval_id": spec.human_approval_ref,
            "expected_action": spec.human_approval_action,
            "expected_target_type": "task-contract",
            "expected_target_id": spec.task_id,
            "expected_target_hash": spec.contract_hash,
            "expected_scope": task_approval_scope(spec),
        }

    def _approval_is_available(self, spec: TaskSpec) -> bool:
        request = self._approval_request(spec)
        if request is None:
            return True
        if self.approvals is None:
            return False
        try:
            self.approvals.verify_registered(request)
        except ApprovalRejected:
            return False
        return True

    def register_task(self, spec: TaskSpec) -> None:
        event = make_event(
            event_type="task_registered",
            reason_code="schema_policy_passed",
            actor_role="scheduler",
            task_id=spec.task_id,
            work_package_id=spec.work_package_id,
            next_state="ready",
            contract_hash=spec.contract_hash,
            context_hash=spec.context_hash,
            repository_commits=[spec.base_commit],
        )
        payload = json.dumps(spec.to_payload(), sort_keys=True, separators=(",", ":"))

        def mutation(connection: sqlite3.Connection) -> dict[str, str]:
            existing = connection.execute(
                "SELECT contract_hash, context_hash, base_commit FROM tasks WHERE task_id = ?",
                (spec.task_id,),
            ).fetchone()
            if existing is not None:
                if tuple(existing) != (spec.contract_hash, spec.context_hash, spec.base_commit):
                    raise PlanningError("task identity is already registered with different hashes", task_id=spec.task_id)
                return {"task_id": spec.task_id, "state": "ready"}
            connection.execute(
                "INSERT OR IGNORE INTO work_packages(work_package_id, state) VALUES (?, 'active')",
                (spec.work_package_id,),
            )
            connection.execute(
                "INSERT INTO tasks(task_id, work_package_id, state, contract_hash, context_hash, "
                "base_commit, capability_class, risk, payload_json) "
                "VALUES (?, ?, 'ready', ?, ?, ?, ?, ?, ?)",
                (
                    spec.task_id, spec.work_package_id, spec.contract_hash, spec.context_hash,
                    spec.base_commit, spec.capability_class, spec.risk, payload,
                ),
            )
            for dependency in spec.dependencies:
                connection.execute(
                    "INSERT INTO dependencies(task_id, depends_on_task_id) VALUES (?, ?)",
                    (spec.task_id, dependency),
                )
            for interface_id, interface_hash in sorted((spec.interface_pins or {}).items()):
                connection.execute(
                    "INSERT INTO dependencies(task_id, interface_id, interface_hash) VALUES (?, ?, ?)",
                    (spec.task_id, interface_id, interface_hash),
                )
            connection.execute(
                "INSERT INTO budgets(scope_type, scope_id, attempts_remaining, "
                "artifact_corrections_remaining) VALUES ('task', ?, ?, ?)",
                (spec.task_id, spec.maximum_attempts, spec.maximum_artifact_corrections),
            )
            return {"task_id": spec.task_id, "state": "ready"}

        self.store.execute(f"task-register:{spec.task_id}:{spec.contract_hash}", event, mutation)

    def _spec(self, task_id: str) -> TaskSpec:
        rows = self.store.rows("SELECT payload_json FROM tasks WHERE task_id = ?", (task_id,))
        if not rows:
            raise PlanningError("unknown task", task_id=task_id)
        return TaskSpec.from_payload(json.loads(rows[0]["payload_json"]))

    def _admission_facts(
        self,
        spec: TaskSpec,
        *,
        worker_capability: str,
        reviewer_available: bool,
    ) -> AdmissionFacts:
        incomplete = int(self.store.scalar(
            "SELECT COUNT(*) FROM dependencies d LEFT JOIN tasks t "
            "ON t.task_id = d.depends_on_task_id WHERE d.task_id = ? "
            "AND d.depends_on_task_id != '' AND (t.state IS NULL OR t.state != 'completed')",
            (spec.task_id,),
        ) or 0)
        active_specs: list[TaskSpec] = []
        for row in self.store.rows(
            "SELECT t.payload_json FROM tasks t JOIN leases l USING(task_id) "
            "WHERE l.active = 1 AND t.task_id != ?",
            (spec.task_id,),
        ):
            active_specs.append(TaskSpec.from_payload(json.loads(row["payload_json"])))
        path_available = not any(
            write_sets_conflict(spec.writable_paths, other.writable_paths)
            for other in active_specs
        )
        lock_ids = tuple(spec.semantic_locks)
        semantic_available = not lock_ids or not bool(self.store.scalar(
            f"SELECT 1 FROM semantic_locks WHERE lock_id IN ({','.join('?' for _ in lock_ids)}) "
            "AND owner_task_id != ? LIMIT 1",
            (*lock_ids, spec.task_id),
        ))
        attempts = int(self.store.scalar(
            "SELECT attempts_remaining FROM budgets WHERE scope_type = 'task' AND scope_id = ?",
            (spec.task_id,),
        ) or 0)
        enforcement_ok = not (
            spec.enforcement_class == "unsandboxed_tools"
            and spec.risk in {"high", "critical"}
        )
        return AdmissionFacts(
            dependencies_complete=incomplete == 0,
            context_current=len(spec.context_hash) == 64 and set(spec.context_hash) != {"0"},
            base_commit_compatible=len(spec.base_commit) == 40 and spec.base_commit != "unbound",
            path_locks_available=path_available,
            semantic_locks_available=semantic_available,
            capability_sufficient=_CAPABILITY_RANK.get(worker_capability, -1) >= _CAPABILITY_RANK.get(spec.capability_class, 99),
            reviewer_available=reviewer_available,
            budget_available=attempts > 0,
            enforcement_compatible=enforcement_ok,
            human_approval_available=self._approval_is_available(spec),
        )

    def dispatch(
        self,
        task_id: str,
        *,
        worker_identity: str,
        worker_capability: str | None = None,
        reviewer_available: bool = True,
    ) -> Lease:
        self.leases.expire_dead()
        spec = self._spec(task_id)
        facts = self._admission_facts(
            spec,
            worker_capability=worker_capability or spec.capability_class,
            reviewer_available=reviewer_available,
        )
        decision = admission_decision(facts)
        if not decision.allowed:
            event = make_event(
                event_type="admission_rejected",
                reason_code=str(decision.reason_code),
                actor_role="scheduler",
                task_id=spec.task_id,
                work_package_id=spec.work_package_id,
                prior_state=self.store.status(spec.task_id)["state"],
                next_state=self.store.status(spec.task_id)["state"],
                contract_hash=spec.contract_hash,
                context_hash=spec.context_hash,
            )
            self.store.execute(
                f"admission-rejected:{spec.task_id}:{decision.reason_code}:{uuid.uuid4()}",
                event,
                lambda _connection: {
                    "task_id": spec.task_id,
                    "reason_code": decision.reason_code,
                },
            )
            if decision.reason_code == "budget_exhausted":
                raise BudgetExhausted("task was not admitted", task_id=task_id)
            raise PlanningError("task was not admitted", task_id=task_id, reason_code=decision.reason_code)
        approval_request = self._approval_request(spec)
        return self.leases.issue(
            spec,
            worker_identity=worker_identity,
            approval_manager=self.approvals,
            approval_request=approval_request,
        )

    def acknowledge(self, task_id: str, context_hash: str) -> Lease:
        return self.leases.acknowledge(task_id, context_hash)

    def expire_lease(self, task_id: str, *, force: bool = False) -> bool:
        return self.leases.expire(task_id, force=force)

    def ready(self) -> list[dict[str, Any]]:
        result = []
        for row in self.store.rows("SELECT task_id, state FROM tasks ORDER BY task_id"):
            spec = self._spec(row["task_id"])
            decision = admission_decision(self._admission_facts(
                spec,
                worker_capability=spec.capability_class,
                reviewer_available=True,
            ))
            is_schedulable_state = row["state"] == "ready"
            result.append({
                "task_id": spec.task_id,
                "capability_class": spec.capability_class,
                "risk": spec.risk,
                "locks": sorted(spec.semantic_locks),
                "state": row["state"],
                "ready": is_schedulable_state and decision.allowed,
                "reason_code": (
                    decision.reason_code
                    if is_schedulable_state
                    else f"state_{row['state']}"
                ),
            })
        return result

    def record_ready_snapshot(self, rows: Sequence[Mapping[str, Any]]) -> None:
        ready_ids = sorted(str(row["task_id"]) for row in rows if row.get("ready") is True)
        if len(ready_ids) < 2:
            return
        work_packages = {
            str(self._spec(task_id).work_package_id) for task_id in ready_ids
        }
        for work_package_id in sorted(work_packages):
            selected = [
                task_id
                for task_id in ready_ids
                if self._spec(task_id).work_package_id == work_package_id
            ]
            if len(selected) < 2:
                continue
            event = make_event(
                event_type="ready_set_computed",
                reason_code="multiple_ready_tasks",
                actor_role="scheduler",
                work_package_id=work_package_id,
                artifact_refs=selected,
            )
            self.store.execute(
                f"ready-snapshot:{work_package_id}:{','.join(selected)}",
                event,
                lambda _connection, tasks=selected: {"ready": tasks},
            )

    def bump_interface(self, interface_id: str) -> tuple[str, ...]:
        """Invalidate direct consumers and their dependency descendants."""
        direct: set[str] = set()
        for row in self.store.rows("SELECT task_id, payload_json FROM tasks"):
            spec = TaskSpec.from_payload(json.loads(row["payload_json"]))
            if interface_id in (spec.interface_pins or {}) or interface_id in spec.semantic_locks:
                direct.add(spec.task_id)
        pending = list(direct)
        affected = set(direct)
        while pending:
            parent = pending.pop()
            for row in self.store.rows(
                "SELECT task_id FROM dependencies WHERE depends_on_task_id = ?", (parent,)
            ):
                if row["task_id"] not in affected:
                    affected.add(row["task_id"])
                    pending.append(row["task_id"])
        for task_id in sorted(affected):
            current = self.store.status(task_id)["state"]
            if current in {state.value for state in TERMINAL_STATES}:
                continue
            event = make_event(
                event_type="dependency_changed", reason_code="context_stale",
                actor_role="scheduler", task_id=task_id,
                prior_state=current, next_state="stale", artifact_refs=[interface_id],
            )
            def mutation(connection: sqlite3.Connection, selected: str = task_id) -> str:
                active = connection.execute(
                    "SELECT lease_id, attempt_id FROM leases WHERE task_id = ? AND active = 1",
                    (selected,),
                ).fetchone()
                if active is not None:
                    connection.execute("UPDATE leases SET active = 0 WHERE lease_id = ?", (active["lease_id"],))
                    connection.execute("DELETE FROM semantic_locks WHERE lease_id = ?", (active["lease_id"],))
                    connection.execute(
                        "UPDATE attempts SET state = 'stale', ended_at = CURRENT_TIMESTAMP WHERE attempt_id = ?",
                        (active["attempt_id"],),
                    )
                connection.execute("UPDATE tasks SET state = 'stale', updated_at = CURRENT_TIMESTAMP WHERE task_id = ?", (selected,))
                return "stale"
            self.store.execute(f"interface-bump:{interface_id}:{task_id}:{uuid.uuid4()}", event, mutation)
        return tuple(sorted(affected))

    def handle_schema_error(self, task_id: str, errors: Sequence[Mapping[str, Any]]) -> str:
        del errors  # Never persist potentially sensitive provider output in telemetry.
        self.leases.expire_dead()
        lease_rows = self.store.rows(
            "SELECT * FROM leases WHERE task_id = ? AND active = 1 ORDER BY epoch DESC LIMIT 1",
            (task_id,),
        )
        if not lease_rows:
            raise IllegalTransition("schema error has no active attempt", task_id=task_id)
        lease = lease_rows[0]
        corrections = int(self.store.scalar(
            "SELECT artifact_corrections_remaining FROM budgets "
            "WHERE scope_type = 'task' AND scope_id = ?", (task_id,),
        ) or 0)
        if corrections > 0:
            event = make_event(
                event_type="schema_correction_requested", reason_code="invalid_artifact",
                actor_role="scheduler", task_id=task_id, attempt_id=lease["attempt_id"],
                prior_state="running", next_state="running",
            )
            def correction(connection: sqlite3.Connection) -> str:
                connection.execute(
                    "UPDATE budgets SET artifact_corrections_remaining = artifact_corrections_remaining - 1 "
                    "WHERE scope_type = 'task' AND scope_id = ?", (task_id,),
                )
                return "correction_pending"
            return str(self.store.execute(f"schema-correction:{lease['lease_id']}", event, correction))

        event = make_event(
            event_type="submission_failed", reason_code="invalid_artifact",
            actor_role="scheduler", task_id=task_id, attempt_id=lease["attempt_id"],
            prior_state="running", next_state="repair_required",
        )
        def failure(connection: sqlite3.Connection) -> str:
            self._end_active_attempt(connection, lease, "failed", "repair_required")
            return "attempt_failed"
        return str(self.store.execute(f"schema-failed:{lease['lease_id']}", event, failure))

    def ingest_manifest(self, task_id: str, manifest: Mapping[str, Any]) -> dict[str, Any]:
        spec = self._spec(task_id)
        self.leases.expire_dead()
        lease_rows = self.store.rows(
            "SELECT * FROM leases WHERE task_id = ? AND active = 1 ORDER BY epoch DESC LIMIT 1",
            (task_id,),
        )
        if not lease_rows:
            self._record_orphan(task_id, manifest)
            raise ContractStale("artifact belongs to an expired or superseded lease", task_id=task_id)
        lease = lease_rows[0]
        mismatch = self._manifest_mismatch(spec, lease, manifest)
        if mismatch is not None:
            event = make_event(
                event_type="lease_revoked", reason_code="permission_violation",
                actor_role="scheduler", task_id=task_id, attempt_id=lease["attempt_id"],
                prior_state=self.store.status(task_id)["state"], next_state="repair_required",
                contract_hash=spec.contract_hash, context_hash=spec.context_hash,
            )
            def revoke(connection: sqlite3.Connection) -> dict[str, str]:
                self._end_active_attempt(connection, lease, "revoked", "repair_required")
                return {"task_id": task_id, "mismatch": mismatch}
            self.store.execute(f"permission-violation:{lease['lease_id']}:{mismatch}", event, revoke)
            raise PermissionViolation("manifest integrity check failed", task_id=task_id, mismatch=mismatch)

        artifact_id = str(uuid.uuid4())
        event = make_event(
            event_type="candidate_ingested", reason_code="candidate_ingested",
            actor_role="scheduler", task_id=task_id, attempt_id=lease["attempt_id"],
            prior_state=self.store.status(task_id)["state"], next_state="candidate",
            contract_hash=spec.contract_hash, context_hash=spec.context_hash,
            repository_commits=[str(manifest["result_commit"])], artifact_refs=[artifact_id],
        )
        def accept(connection: sqlite3.Connection) -> dict[str, Any]:
            connection.execute("UPDATE leases SET active = 0 WHERE lease_id = ?", (lease["lease_id"],))
            connection.execute("DELETE FROM semantic_locks WHERE lease_id = ?", (lease["lease_id"],))
            connection.execute(
                "UPDATE attempts SET state = 'candidate', result_commit = ?, ended_at = CURRENT_TIMESTAMP WHERE attempt_id = ?",
                (manifest["result_commit"], lease["attempt_id"]),
            )
            connection.execute("UPDATE tasks SET state = 'candidate', updated_at = CURRENT_TIMESTAMP WHERE task_id = ?", (task_id,))
            connection.execute(
                "INSERT INTO artifacts(artifact_id, artifact_type, task_id, attempt_id, path, integrable, payload_json) "
                "VALUES (?, 'implementation-manifest', ?, ?, '', 1, ?)",
                (artifact_id, task_id, lease["attempt_id"], json.dumps(dict(manifest), sort_keys=True, separators=(",", ":"))),
            )
            return {"artifact_id": artifact_id, "task_id": task_id, "state": "candidate"}
        return dict(self.store.execute(f"candidate-ingest:{artifact_id}", event, accept))

    def _manifest_mismatch(self, spec: TaskSpec, lease: Mapping[str, Any], manifest: Mapping[str, Any]) -> str | None:
        exact = (
            ("fencing", "fencing_token", lease["fencing_token"]),
            ("contract", "contract_hash", spec.contract_hash),
            ("context", "context_hash", spec.context_hash),
            ("provenance", "base_commit", spec.base_commit),
            ("lease", "lease_id", lease["lease_id"]),
            ("attempt", "attempt_id", lease["attempt_id"]),
            ("epoch", "lease_epoch", lease["epoch"]),
        )
        for name, field, expected in exact:
            if manifest.get(field) != expected:
                return name
        for changed in manifest.get("files_changed", ()):
            path = changed.get("path") if isinstance(changed, Mapping) else None
            if not isinstance(path, str) or any(character in path for character in "*?["):
                return "prohibited_path"
            try:
                prohibited = any(path_patterns_overlap(path, pattern) for pattern in spec.prohibited_paths)
                writable = any(path_patterns_overlap(path, pattern) for pattern in spec.writable_paths)
            except ValueError:
                return "prohibited_path"
            if prohibited or not writable:
                return "prohibited_path"
        return None

    def _record_orphan(self, task_id: str, manifest: Mapping[str, Any]) -> None:
        artifact_id = str(uuid.uuid4())
        attempt_id = str(manifest.get("attempt_id", "unknown-attempt"))
        event = make_event(
            event_type="late_artifact", reason_code="stale_fencing_token",
            actor_role="scheduler", task_id=task_id, attempt_id=attempt_id,
            prior_state=self.store.status(task_id)["state"], next_state=self.store.status(task_id)["state"],
            artifact_refs=[artifact_id],
        )
        def mutation(connection: sqlite3.Connection) -> dict[str, str]:
            connection.execute("UPDATE tasks SET orphan_evidence_count = orphan_evidence_count + 1 WHERE task_id = ?", (task_id,))
            connection.execute(
                "INSERT INTO artifacts(artifact_id, artifact_type, task_id, attempt_id, path, integrable, payload_json) "
                "VALUES (?, 'implementation-manifest', ?, ?, '', 0, ?)",
                (artifact_id, task_id, attempt_id, json.dumps(dict(manifest), sort_keys=True, separators=(",", ":"))),
            )
            return {"artifact_id": artifact_id, "task_id": task_id}
        self.store.execute(f"orphan:{artifact_id}", event, mutation)

    @staticmethod
    def _end_active_attempt(
        connection: sqlite3.Connection,
        lease: Mapping[str, Any],
        attempt_state: str,
        task_state: str,
    ) -> None:
        connection.execute("UPDATE leases SET active = 0 WHERE lease_id = ?", (lease["lease_id"],))
        connection.execute("DELETE FROM semantic_locks WHERE lease_id = ?", (lease["lease_id"],))
        connection.execute(
            "UPDATE attempts SET state = ?, ended_at = CURRENT_TIMESTAMP WHERE attempt_id = ?",
            (attempt_state, lease["attempt_id"]),
        )
        connection.execute(
            "UPDATE tasks SET state = ?, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?",
            (task_state, lease["task_id"]),
        )
