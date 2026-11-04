"""Deterministic task state machine, admission checks, and artifact ingestion."""

from __future__ import annotations

import json
import hashlib
import os
import pathlib
import shutil
import sqlite3
import subprocess
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

from .approval import ApprovalManager, ApprovalRejected
from .check_environment import (
    DEFAULT_CHECK_TIMEOUT_SECONDS,
    ISOLATION_LEGACY,
    CandidateTreeError,
    CheckEnvironmentUnavailable,
    legacy_environment,
    require_sandbox,
    run_candidate_checks,
    selected_isolation,
)
from .errors import (
    BudgetExhausted,
    ContractStale,
    IllegalTransition,
    PermissionViolation,
    PlanningError,
)
from .events import make_event
from .gate_runner import run_checks
from .interfaces import binding_from_source, current_interface_binding
from .hashing import sha256_of_json
from .leases import Lease, LeaseManager
from .locks import path_patterns_overlap, semantic_lock_claims, write_sets_conflict
from .models import TERMINAL_STATES, TaskState
from .store import EventStore

__all__ = [
    "ADMISSION_CONDITIONS",
    "CAPABILITY_RANK",
    "CHECK_TIMEOUT_SECONDS",
    "GIT_HARDENING_ARGS",
    "SCHEDULER_CYCLE",
    "TRANSITION_RULES",
    "AdmissionDecision",
    "AdmissionFacts",
    "Scheduler",
    "TaskSpec",
    "admission_decision",
    "hardened_git_environment",
    "run_hardened_git",
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
    TransitionRule((TaskState.STALE,), "context_rebuilt", (TaskState.VALIDATED,)),
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
    deterministic_checks: tuple[str, ...] = ()
    human_approval_ref: str | None = None
    human_approval_action: str | None = None
    #: ``dependencies.integration_order`` of the contract; None for payloads registered before it was carried.
    integration_order: int | None = None

    def with_semantic_locks(self, claims: Mapping[str, str]) -> "TaskSpec":
        return replace(self, semantic_locks=dict(claims))

    def to_payload(self) -> dict[str, Any]:
        value = asdict(self)
        value["writable_paths"] = list(self.writable_paths)
        value["prohibited_paths"] = list(self.prohibited_paths)
        value["dependencies"] = list(self.dependencies)
        value["deterministic_checks"] = list(self.deterministic_checks)
        return value

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> "TaskSpec":
        data = dict(value)
        data["writable_paths"] = tuple(data.get("writable_paths", ()))
        data["prohibited_paths"] = tuple(data.get("prohibited_paths", ()))
        data["dependencies"] = tuple(data.get("dependencies", ()))
        data["deterministic_checks"] = tuple(data.get("deterministic_checks", ()))
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


#: Public name for routing's reviewer-floor comparisons.
CAPABILITY_RANK = _CAPABILITY_RANK

#: Wall-clock limit for each contract-owned deterministic check. A check that reaches it
#: is a failing check (exit_code 124, ``timed_out: true``), never a hung verifier. By default checks
#: run in the runc sandbox of ``check_environment`` (the one contract shared with ``integrate``);
#: ``AGENTCTL_CHECK_ISOLATION=legacy-unsafe`` selects the old ``gate_runner`` path instead.
CHECK_TIMEOUT_SECONDS = DEFAULT_CHECK_TIMEOUT_SECONDS

#: Only these contract risks may have their deterministic checks run, under ANY isolation (AGENTS.md section 7:
#: unsandboxed tools are prohibited for high-risk tasks; the runc boundary has had one independent review that
#: found blocking issues, so high and critical stay refused until the boundary has had a further independent
#: review and an owner decision). An allow-list: any other value, however spelled, is refused. The named
#: reasons are what verification and integration record.
GATE_ALLOWED_RISKS = frozenset({"low", "medium"})
UNSANDBOXED_GATE_ALLOWED_RISKS = GATE_ALLOWED_RISKS  # earlier name, kept for importers
GATE_RISK_NOT_PERMITTED = "gate_risk_not_permitted_until_sandbox_reviewed"
UNSANDBOXED_GATE_UNKNOWN_RISK = "unsandboxed_gate_unknown_risk"
NO_DETERMINISTIC_CHECKS = "no_deterministic_checks"


def gate_risk_refusal(risk: object) -> str | None:
    """The named reason checks may not run for ``risk``, or ``None`` when low or medium."""
    normalized = str(risk).strip().lower() if risk is not None else ""
    if normalized in GATE_ALLOWED_RISKS:
        return None
    return GATE_RISK_NOT_PERMITTED if normalized in {"high", "critical"} else UNSANDBOXED_GATE_UNKNOWN_RISK


unsandboxed_gate_refusal = gate_risk_refusal  # earlier name, kept for importers


def effective_checks(checks: Sequence[object] | None) -> tuple[str, ...]:
    """The runnable checks: non-blank strings. Blank or non-string entries verify nothing (``sh -c ""``
    exits 0), so they are ignored next to a real check and never run; all-blank is vacuous."""
    return tuple(item for item in (checks or ()) if isinstance(item, str) and item.strip())

#: Options for every git command the control plane runs. A worktree's ``.git`` links to the
#: SHARED git directory, so anything a worker or a gate check plants in the repository config
#: (``diff.external``, ``core.fsmonitor``, a pager) would otherwise execute later under the
#: control plane's privileges. Diff-like commands additionally pass ``--no-ext-diff
#: --no-textconv`` (an empty ``diff.external`` alone makes a plain ``git diff`` fail).
GIT_HARDENING_ARGS: tuple[str, ...] = (
    "--no-pager",
    "-c", "core.fsmonitor=false",
    "-c", "diff.external=",
    "-c", "core.hooksPath=/dev/null",
)


def hardened_git_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Scrubbed environment for control-plane git: no inherited config, credentials, or ``GIT_*``."""
    git_path = shutil.which("git")
    path = ["/usr/local/bin", "/usr/bin", "/bin"]
    if git_path:
        path.insert(0, os.path.dirname(os.path.realpath(git_path)))
    env = {
        "PATH": os.pathsep.join(dict.fromkeys(path)),
        "HOME": "/nonexistent",
        "XDG_CONFIG_HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "LANG": "C",
    }
    env.update(extra or {})
    return env


def run_hardened_git(
    args: Sequence[str],
    cwd: str | os.PathLike[str],
    *,
    text: bool = True,
    env: Mapping[str, str] | None = None,
) -> "subprocess.CompletedProcess[Any]":
    """Run ``git <hardening> <args>`` with a scrubbed environment."""
    executable = shutil.which("git") or "git"
    return subprocess.run(
        [executable, *GIT_HARDENING_ARGS, *args],
        cwd=cwd,
        capture_output=True,
        text=text,
        check=False,
        env=hardened_git_environment(env),
    )


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
        interface_bindings = {}
        for interface_id, expected_version in sorted((spec.interface_pins or {}).items()):
            binding = current_interface_binding(
                self.store.repository_root, interface_id, self.store
            )
            if binding.version != expected_version:
                raise PlanningError(
                    "task contract pins a noncurrent interface version",
                    task_id=spec.task_id,
                    interface_id=interface_id,
                    expected_version=expected_version,
                    current_version=binding.version,
                )
            interface_bindings[interface_id] = binding
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
            for interface_id, binding in sorted(interface_bindings.items()):
                connection.execute(
                    "INSERT OR IGNORE INTO artifacts(artifact_id, artifact_type, content_hash, "
                    "path, integrable, payload_json) VALUES (?, 'interface-binding', ?, ?, 1, ?)",
                    (
                        f"interface-binding:{interface_id}",
                        binding.content_hash,
                        binding.path,
                        json.dumps(binding.to_payload(), sort_keys=True, separators=(",", ":")),
                    ),
                )
                connection.execute(
                    "INSERT INTO dependencies(task_id, interface_id, interface_hash) VALUES (?, ?, ?)",
                    (spec.task_id, interface_id, binding.content_hash),
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

    def task_spec(self, task_id: str) -> TaskSpec:
        """The registered, immutable spec of ``task_id`` (read-only)."""
        return self._spec(task_id)

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
            base_commit_compatible=self._base_commit_compatible(spec.base_commit),
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
        current_lock_claims = semantic_lock_claims(
            spec.semantic_locks.keys(), self.store.repository_root
        )
        if dict(spec.semantic_locks) != current_lock_claims:
            raise ContractStale(
                "semantic lock source changed after task validation",
                task_id=task_id,
                expected=dict(spec.semantic_locks),
                actual=current_lock_claims,
            )
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

    def bump_interface(
        self, interface_id: str, source_path: str, version: str
    ) -> tuple[str, ...]:
        """Bind new interface content and invalidate mismatched consumers and descendants."""
        binding = binding_from_source(
            self.store.repository_root, interface_id, version, source_path
        )
        prior_rows = self.store.rows(
            "SELECT payload_json FROM artifacts WHERE artifact_id = ?",
            (f"interface-binding:{interface_id}",),
        )
        prior_hash = "unregistered"
        if prior_rows:
            prior_hash = str(json.loads(prior_rows[0]["payload_json"])["content_hash"])
        binding_event = make_event(
            event_type="interface_updated",
            reason_code="interface_binding_changed",
            actor_role="scheduler",
            artifact_refs=[
                f"{interface_id}@{version}",
                f"old:{prior_hash}",
                f"new:{binding.content_hash}",
                source_path,
            ],
        )

        def bind(connection: sqlite3.Connection) -> dict[str, str]:
            connection.execute(
                "INSERT INTO artifacts(artifact_id, artifact_type, content_hash, path, "
                "integrable, payload_json) VALUES (?, 'interface-binding', ?, ?, 1, ?) "
                "ON CONFLICT(artifact_id) DO UPDATE SET content_hash = excluded.content_hash, "
                "path = excluded.path, integrable = 1, payload_json = excluded.payload_json",
                (
                    f"interface-binding:{interface_id}",
                    binding.content_hash,
                    binding.path,
                    json.dumps(binding.to_payload(), sort_keys=True, separators=(",", ":")),
                ),
            )
            return {"interface_id": interface_id, "content_hash": binding.content_hash}

        self.store.execute(
            f"interface-bind:{interface_id}:{version}:{binding.content_hash}",
            binding_event,
            bind,
        )

        direct: set[str] = set()
        for row in self.store.rows(
            "SELECT d.task_id, d.interface_hash, t.payload_json FROM dependencies d "
            "JOIN tasks t ON t.task_id = d.task_id WHERE d.interface_id = ?",
            (interface_id,),
        ):
            spec = TaskSpec.from_payload(json.loads(row["payload_json"]))
            expected_version = (spec.interface_pins or {}).get(interface_id)
            if expected_version != version or row["interface_hash"] != binding.content_hash:
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
                prior_state=current,
                next_state="stale",
                artifact_refs=[
                    f"{interface_id}@{version}",
                    f"old:{prior_hash}",
                    f"new:{binding.content_hash}",
                ],
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

    def fail_attempt(self, task_id: str, reason_code: str) -> str:
        """End a running attempt whose worker output was received but is unusable.

        Used for evaluated failures that existing paths do not cover (a scope-audit
        rejection, an unusable proposal). ``running -> repair_required`` while the
        task still has attempts left, ``-> escalated`` when none remain (both are the
        permitted outcomes of ``submission_failed``). The lease and its semantic locks
        are released and the attempt is marked ``failed``. Never advances toward a
        candidate. Infrastructure failures that evaluated nothing use
        :meth:`expire_lease` instead, and malformed artifacts use
        :meth:`handle_schema_error`.
        """
        self.leases.expire_dead()
        lease_rows = self.store.rows(
            "SELECT * FROM leases WHERE task_id = ? AND active = 1 ORDER BY epoch DESC LIMIT 1",
            (task_id,),
        )
        if not lease_rows:
            raise IllegalTransition("attempt failure has no active attempt", task_id=task_id)
        lease = lease_rows[0]
        current = str(self.store.status(task_id)["state"])
        remaining = int(self.store.scalar(
            "SELECT attempts_remaining FROM budgets WHERE scope_type = 'task' AND scope_id = ?",
            (task_id,),
        ) or 0)
        outcome = TaskState.REPAIR_REQUIRED if remaining > 0 else TaskState.ESCALATED
        transition(current, "submission_failed", outcome)
        event = make_event(
            event_type="submission_failed", reason_code=reason_code,
            actor_role="scheduler", task_id=task_id, attempt_id=lease["attempt_id"],
            prior_state=current, next_state=outcome.value,
        )

        def failure(connection: sqlite3.Connection) -> str:
            row = connection.execute(
                "SELECT state FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None or row["state"] != current:
                raise IllegalTransition("task changed before attempt failure", task_id=task_id)
            self._end_active_attempt(connection, lease, "failed", outcome.value)
            return outcome.value

        return str(self.store.execute(f"attempt-failed:{lease['lease_id']}", event, failure))

    def record_model_run(
        self,
        *,
        run_id: str,
        task_id: str,
        attempt_id: str,
        provider: str,
        model_version: str | None,
        status: str,
        usage: Mapping[str, Any] | None,
        summary: Mapping[str, Any],
    ) -> str:
        """Persist one provider invocation (success or failure) in ``model_runs``.

        ``run_id`` is the control-plane-minted run identity. ``usage`` is the
        provider's raw usage object (``{}`` when none was reported, never zeros).
        ``summary`` must already be redacted and free of prompts and document text.
        """
        event = make_event(
            event_type="model_run_recorded", reason_code=status, actor_role="scheduler",
            task_id=task_id, attempt_id=attempt_id, provider=provider,
            model_version=model_version, artifact_refs=[f"model-run:{run_id}"],
        )

        def insert(connection: sqlite3.Connection) -> str:
            connection.execute(
                "INSERT INTO model_runs(run_id, task_id, attempt_id, provider, model_version, "
                "status, usage_json, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id, task_id, attempt_id, provider, model_version, status,
                    json.dumps(dict(usage or {}), sort_keys=True, separators=(",", ":")),
                    json.dumps(dict(summary), sort_keys=True, separators=(",", ":")),
                ),
            )
            return run_id

        return str(self.store.execute(f"model-run:{run_id}", event, insert))

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
        if self._names_superseded_attempt(task_id, lease, manifest):
            # Design 14.1: a submission from an expired or superseded attempt is orphan evidence and
            # advances nothing. It must not touch the active lease or the task state.
            self._record_orphan(task_id, manifest)
            raise ContractStale("artifact belongs to an expired or superseded lease", task_id=task_id)
        current_state = str(self.store.status(task_id)["state"])
        transition(current_state, "candidate_ingested", TaskState.CANDIDATE)
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

        try:
            binding = self._git_candidate_binding(spec, lease, manifest)
        except PermissionViolation as exc:
            mismatch = str(exc.context.get("mismatch", "provenance"))
            event = make_event(
                event_type="lease_revoked", reason_code="permission_violation",
                actor_role="scheduler", task_id=task_id, attempt_id=lease["attempt_id"],
                prior_state=self.store.status(task_id)["state"], next_state="repair_required",
                contract_hash=spec.contract_hash, context_hash=spec.context_hash,
            )

            def revoke_provenance(connection: sqlite3.Connection) -> dict[str, str]:
                self._end_active_attempt(connection, lease, "revoked", "repair_required")
                return {"task_id": task_id, "mismatch": mismatch}

            self.store.execute(
                f"permission-violation:{lease['lease_id']}:{mismatch}",
                event,
                revoke_provenance,
            )
            raise
        artifact_id = str(uuid.uuid4())
        binding_id = f"candidate-binding:{task_id}:{lease['attempt_id']}"
        event = make_event(
            event_type="candidate_ingested", reason_code="candidate_ingested",
            actor_role="scheduler", task_id=task_id, attempt_id=lease["attempt_id"],
            prior_state=current_state, next_state="candidate",
            contract_hash=spec.contract_hash, context_hash=spec.context_hash,
            repository_commits=[str(manifest["result_commit"])],
            artifact_refs=[artifact_id, binding_id],
        )
        def accept(connection: sqlite3.Connection) -> dict[str, Any]:
            state = connection.execute(
                "SELECT state FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if state is None or state["state"] != current_state:
                raise IllegalTransition(
                    "task changed before candidate ingestion", task_id=task_id
                )
            transition(state["state"], "candidate_ingested", TaskState.CANDIDATE)
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
            connection.execute(
                "INSERT INTO artifacts(artifact_id, artifact_type, task_id, attempt_id, "
                "content_hash, path, integrable, payload_json) VALUES "
                "(?, 'candidate-binding', ?, ?, ?, '', 1, ?)",
                (
                    binding_id,
                    task_id,
                    lease["attempt_id"],
                    binding["diff_hash"],
                    json.dumps(binding, sort_keys=True, separators=(",", ":")),
                ),
            )
            return {"artifact_id": artifact_id, "task_id": task_id, "state": "candidate"}
        return dict(self.store.execute(f"candidate-ingest:{artifact_id}", event, accept))

    def _names_superseded_attempt(
        self, task_id: str, active: Mapping[str, Any], manifest: Mapping[str, Any]
    ) -> bool:
        """True when the manifest names an earlier or non-current attempt of this task and NOT the active one.

        A manifest that claims the active lease or the active attempt (by id) is judged by
        ``_manifest_mismatch`` instead: a wrong token or hash on the CURRENT lease is a permission violation.
        """
        lease_id, attempt_id = manifest.get("lease_id"), manifest.get("attempt_id")
        if lease_id == active["lease_id"] or attempt_id == active["attempt_id"]:
            return False
        epoch = manifest.get("lease_epoch")
        if isinstance(epoch, int) and not isinstance(epoch, bool) and epoch < active["epoch"]:
            return True
        if isinstance(lease_id, str) and self.store.rows(
            "SELECT 1 FROM leases WHERE task_id = ? AND lease_id = ?", (task_id, lease_id)
        ):
            return True
        return isinstance(attempt_id, str) and bool(self.store.rows(
            "SELECT 1 FROM attempts WHERE task_id = ? AND attempt_id = ?", (task_id, attempt_id)
        ))

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

    def _git(self, *args: str, cwd: str | None = None, text: bool = True) -> subprocess.CompletedProcess[Any]:
        return run_hardened_git(args, cwd or self.store.repository_root, text=text)

    def _base_commit_compatible(self, base_commit: str) -> bool:
        if len(base_commit) != 40 or base_commit == "unbound":
            return False
        exists = self._git("cat-file", "-e", f"{base_commit}^{{commit}}")
        if exists.returncode != 0:
            return False
        ancestor = self._git("merge-base", "--is-ancestor", base_commit, "HEAD")
        return ancestor.returncode == 0

    def _git_candidate_binding(
        self,
        spec: TaskSpec,
        lease: Mapping[str, Any],
        manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        result_commit = str(manifest.get("result_commit", ""))
        worktree = (self.store.repository_root / str(lease["worktree_path"])).resolve()
        checks = (
            self._git("cat-file", "-e", f"{spec.base_commit}^{{commit}}"),
            self._git("cat-file", "-e", f"{result_commit}^{{commit}}"),
            self._git("merge-base", "--is-ancestor", spec.base_commit, result_commit),
        )
        if any(check.returncode != 0 for check in checks) or not worktree.is_dir():
            raise PermissionViolation(
                "manifest integrity check failed", task_id=spec.task_id, mismatch="provenance"
            )
        top = self._git("rev-parse", "--show-toplevel", cwd=str(worktree))
        head = self._git("rev-parse", "HEAD", cwd=str(worktree))
        clean = self._git("status", "--porcelain", cwd=str(worktree))
        if (
            top.returncode != 0
            or pathlib.Path(top.stdout.strip()).resolve() != worktree
            or head.returncode != 0
            or head.stdout.strip() != result_commit
            or clean.returncode != 0
            or clean.stdout.strip()
        ):
            raise PermissionViolation(
                "manifest integrity check failed", task_id=spec.task_id, mismatch="provenance"
            )
        changed = self._git(
            "diff", "--no-ext-diff", "--no-textconv", "--name-status", "--find-renames",
            spec.base_commit, result_commit
        )
        if changed.returncode != 0:
            raise PermissionViolation(
                "manifest integrity check failed", task_id=spec.task_id, mismatch="provenance"
            )
        actual_files: list[dict[str, str]] = []
        kinds = {"A": "added", "M": "modified", "D": "deleted"}
        for line in changed.stdout.splitlines():
            parts = line.split("\t")
            status = parts[0]
            if status.startswith("R") and len(parts) == 3:
                actual_files.append({"path": parts[2], "change_type": "renamed"})
            elif status[:1] in kinds and len(parts) == 2:
                actual_files.append({"path": parts[1], "change_type": kinds[status[:1]]})
            else:
                raise PermissionViolation(
                    "manifest integrity check failed",
                    task_id=spec.task_id,
                    mismatch="provenance",
                )
        declared = sorted(
            (dict(value) for value in manifest.get("files_changed", ())),
            key=lambda item: (item.get("path", ""), item.get("change_type", "")),
        )
        if sorted(actual_files, key=lambda item: (item["path"], item["change_type"])) != declared:
            raise PermissionViolation(
                "manifest integrity check failed", task_id=spec.task_id, mismatch="provenance"
            )
        tree = self._git("rev-parse", f"{result_commit}^{{tree}}")
        diff = self._git(
            "diff", "--no-ext-diff", "--no-textconv", "--binary", "--full-index",
            spec.base_commit, result_commit, text=False
        )
        if tree.returncode != 0 or diff.returncode != 0:
            raise PermissionViolation(
                "manifest integrity check failed", task_id=spec.task_id, mismatch="provenance"
            )
        return {
            "task_id": spec.task_id,
            "attempt_id": str(lease["attempt_id"]),
            "base_commit": spec.base_commit,
            "result_commit": result_commit,
            "tree_hash": tree.stdout.strip(),
            "diff_hash": hashlib.sha256(diff.stdout).hexdigest(),
            "files_changed": actual_files,
            "worktree_path": str(lease["worktree_path"]),
            "contract_hash": spec.contract_hash,
            "context_hash": spec.context_hash,
        }

    def verify_candidate(self, task_id: str) -> dict[str, Any]:
        """Run contract-owned deterministic checks and bind their evidence to a candidate.

        Checks run in the shared check environment (``check_environment``: the commit's tree in a runc
        sandbox with a synthetic private Git repository), exactly as the integrator reruns them.
        """
        spec = self._spec(task_id)
        isolation = selected_isolation()  # an unrecognised selector is refused, never ignored
        checks = effective_checks(spec.deterministic_checks)
        refusal = gate_risk_refusal(spec.risk)
        if refusal is not None:
            # Refused under every isolation, before any state transition, event or check (see GATE_ALLOWED_RISKS).
            raise PermissionViolation(
                "deterministic checks are not permitted for this task's risk until the sandbox boundary has had a "
                "further independent review and an owner decision",
                task_id=task_id, reason_code=refusal, risk=str(spec.risk)[:40],
            )
        if isolation != ISOLATION_LEGACY and checks:
            require_sandbox()  # fail closed before any transition: reason_code sandbox_unavailable
        status = str(self.store.status(task_id)["state"])
        transition(status, "verification_started", TaskState.DETERMINISTIC_VERIFICATION)
        attempt = self.store.rows(
            "SELECT * FROM attempts WHERE task_id = ? ORDER BY attempt_number DESC LIMIT 1",
            (task_id,),
        )[0]
        binding_row = self.store.rows(
            "SELECT payload_json FROM artifacts WHERE artifact_id = ? AND integrable = 1",
            (f"candidate-binding:{task_id}:{attempt['attempt_id']}",),
        )
        if not binding_row:
            raise IllegalTransition("candidate has no trusted Git binding", task_id=task_id)
        binding = json.loads(binding_row[0]["payload_json"])
        started = make_event(
            event_type="verification_started",
            reason_code="deterministic_verification_started",
            actor_role="verifier",
            task_id=task_id,
            attempt_id=attempt["attempt_id"],
            prior_state="candidate",
            next_state="deterministic_verification",
            repository_commits=[binding["result_commit"]],
        )

        def begin(connection: sqlite3.Connection) -> dict[str, str]:
            current = connection.execute(
                "SELECT state FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if current is None:
                raise IllegalTransition("task disappeared before verification", task_id=task_id)
            transition(current["state"], "verification_started", TaskState.DETERMINISTIC_VERIFICATION)
            connection.execute(
                "UPDATE tasks SET state = 'deterministic_verification', updated_at = CURRENT_TIMESTAMP "
                "WHERE task_id = ?", (task_id,)
            )
            return {"task_id": task_id, "state": "deterministic_verification"}

        self.store.execute(f"verify-start:{task_id}:{attempt['attempt_id']}", started, begin)
        # No (non-blank) check means nothing was verified; `all([])` must never read as a pass.
        vacuous = not checks
        tamper: dict[str, Any] | None = None
        failure_reason: str | None = None
        if vacuous:
            results: list[dict[str, Any]] = []
            environment = legacy_environment("shared-git-link") if isolation == ISOLATION_LEGACY else None
            failure_reason = NO_DETERMINISTIC_CHECKS
        elif isolation == ISOLATION_LEGACY:
            worktree = (self.store.repository_root / binding["worktree_path"]).resolve()
            results, tamper = self._run_gate_checks(worktree, checks)
            environment = legacy_environment("shared-git-link", timeout=CHECK_TIMEOUT_SECONDS)
        else:
            try:
                outcome = run_candidate_checks(
                    self.store.repository_root, binding["result_commit"], checks, label="verify",
                    timeout=CHECK_TIMEOUT_SECONDS,
                    spec_fields={"task_id": task_id, "tree_hash": binding["tree_hash"]},
                )
                results, environment = outcome["checks"], outcome["environment"]
                failure_reason = outcome.get("failure_reason")
            except CandidateTreeError as error:
                results, environment = [], None
                failure_reason = "candidate_tree_unsafe"
                unsafe = {"error": type(error).__name__, "detail": str(error)[:200]}
            except CheckEnvironmentUnavailable as error:
                # The sandbox vanished between the pre-check and the run. The task is already in
                # deterministic_verification, so this is recorded as failing evidence, never a pass.
                results, environment = [], None
                failure_reason = error.reason_code
        passed = (
            not vacuous and not tamper and failure_reason is None
            and all(item["exit_code"] == 0 and not item.get("reason") for item in results)
        )
        evidence = {"candidate_binding": binding, "checks": results, "passed": passed}
        if environment is not None:
            evidence["environment"] = environment
        if failure_reason is not None:
            evidence["failure_reason"] = failure_reason
        if failure_reason == "candidate_tree_unsafe":
            evidence["candidate_tree"] = unsafe
        if tamper:
            evidence["repository_tamper"] = tamper
        evidence_hash = sha256_of_json(evidence)
        next_state = "independent_review" if passed else "repair_required"
        event_name = "checks_passed" if passed else "implementation_defect"
        event = make_event(
            event_type=event_name,
            reason_code=(
                "verification_passed" if passed
                else str(tamper["reason_code"]) if tamper
                else failure_reason if failure_reason else "verification_failed"
            ),
            actor_role="verifier",
            task_id=task_id,
            attempt_id=attempt["attempt_id"],
            prior_state="deterministic_verification",
            next_state=next_state,
            repository_commits=[binding["result_commit"]],
            artifact_refs=[f"verification:{task_id}:{attempt['attempt_id']}"],
        )

        def finish(connection: sqlite3.Connection) -> dict[str, Any]:
            current = connection.execute(
                "SELECT state FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if current is None:
                raise IllegalTransition("task disappeared during verification", task_id=task_id)
            transition(current["state"], event_name, TaskState(next_state))
            connection.execute(
                "INSERT INTO artifacts(artifact_id, artifact_type, task_id, attempt_id, "
                "content_hash, path, integrable, payload_json) VALUES "
                "(?, 'verification-evidence', ?, ?, ?, '', ?, ?)",
                (
                    f"verification:{task_id}:{attempt['attempt_id']}",
                    task_id,
                    attempt["attempt_id"],
                    evidence_hash,
                    1 if passed else 0,
                    json.dumps(evidence, sort_keys=True, separators=(",", ":")),
                ),
            )
            connection.execute(
                "UPDATE tasks SET state = ?, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?",
                (next_state, task_id),
            )
            return {
                "task_id": task_id,
                "state": next_state,
                "verification_evidence_hash": evidence_hash,
            }

        return dict(
            self.store.execute(
                f"verify-finish:{task_id}:{attempt['attempt_id']}:{evidence_hash}",
                event,
                finish,
            )
        )

    def _run_gate_checks(
        self, worktree: pathlib.Path, commands: Sequence[str]
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """LEGACY (``AGENTCTL_CHECK_ISOLATION=legacy-unsafe``) path: run the contract's checks through the
        shared gate runner in the leased worktree and audit the shared ``.git``.

        The leased worktree's ``.git`` links to the repository's shared git directory, so a
        check could plant config (``diff.external``, ``core.fsmonitor``, drivers, hooks, refs)
        that later runs with the control plane's privileges. The same protected-state
        snapshot the integrator uses is taken before and after; any difference fails
        verification with ``gate_tampered_repository``. The candidate's own worktree
        metadata (index, HEAD) is excluded because a check legitimately refreshes it. Nothing
        is restored. Evidence per check keeps the legacy keys (``command``, ``exit_code``,
        ``stdout_hash``, ``stderr_hash``, ``timed_out`` when true) and adds the runner's
        ``reason``, ``left_processes``, ``gate_isolation`` and ``duration_ms``.
        """
        if not commands:
            return [], None
        # integrate.py imports this module at import time, so these helpers are imported lazily.
        from .integrate import DEFAULT_BRANCH, _Git, _changed, _protected_state

        try:
            git = _Git(self.store.repository_root)
            own = git.out("rev-parse", "--absolute-git-dir", cwd=worktree)
            exclude = pathlib.Path(own).resolve() if own else None
            before = _protected_state(git, DEFAULT_BRANCH, exclude)
        except Exception as error:  # fail closed: no check runs without a tamper baseline
            return [], {
                "reason_code": "gate_tamper_check_unavailable", "changed": [], "error": type(error).__name__,
            }
        extra = {"PYTHONPATH": os.environ["PYTHONPATH"]} if "PYTHONPATH" in os.environ else None
        ran = run_checks(list(commands), worktree, CHECK_TIMEOUT_SECONDS, env_extra=extra)
        results: list[dict[str, Any]] = []
        for item in ran:
            entry: dict[str, Any] = {
                "command": item["gate"],
                "exit_code": 124 if item["timed_out"] else item["exit_code"],
                "stdout_hash": item["stdout_hash"],
                "stderr_hash": item["stderr_hash"],
                "reason": item["reason"],
                "left_processes": item["left_processes"],
                "gate_isolation": item["gate_isolation"],
                "duration_ms": item["duration_ms"],
            }
            if item["timed_out"]:
                entry["timed_out"] = True
            results.append(entry)
        try:
            after = _protected_state(git, DEFAULT_BRANCH, exclude)
        except Exception as error:
            return results, {
                "reason_code": "gate_tamper_check_unavailable", "changed": [], "error": type(error).__name__,
            }
        changed = _changed(before, after)
        if not changed:
            return results, None
        return results, {
            "reason_code": "gate_tampered_repository",
            "changed": changed[:50],
            "state_after": sha256_of_json(after),
        }

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
