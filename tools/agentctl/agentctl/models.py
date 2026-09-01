"""Typed, immutable in-memory shapes for the ten control-plane artifacts.

One ``@dataclass(frozen=True)`` per ``artifact_type``, plus the normative task-
state enumeration the scheduler (Task 15) drives its transition table from.

These are convenience views, not the source of truth: the JSON Schema files
under ``agent-system/schemas/`` are authoritative, and every dataclass field
here mirrors a required property of its schema. ``from_mapping`` pulls exactly
the modelled fields off a validated artifact dict and ignores the rest, so a
schema gaining an optional field does not break construction.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, fields
from typing import Any, ClassVar, Mapping, TypeVar

__all__ = [
    "TaskState",
    "TERMINAL_STATES",
    "EXCEPTIONAL_STATES",
    "ArtifactType",
    "Artifact",
    "WorkPackage",
    "TaskContract",
    "ContextManifest",
    "ImplementationManifest",
    "ReviewVerdict",
    "IntegrationManifest",
    "HumanApproval",
    "PhaseGate",
    "Event",
    "ArchitectureAudit",
    "MODEL_BY_ARTIFACT_TYPE",
]


class TaskState(str, enum.Enum):
    """Every state a task may occupy. Order of the happy path is significant."""

    DRAFT = "draft"
    VALIDATED = "validated"
    READY = "ready"
    LEASED = "leased"
    RUNNING = "running"
    CANDIDATE = "candidate"
    DETERMINISTIC_VERIFICATION = "deterministic_verification"
    INDEPENDENT_REVIEW = "independent_review"
    INTEGRATION_READY = "integration_ready"
    INTEGRATING = "integrating"
    COMPLETED = "completed"

    # Exceptional — reachable from many states, not part of the linear path.
    BLOCKED = "blocked"
    REPAIR_REQUIRED = "repair_required"
    REPLAN_REQUIRED = "replan_required"
    ESCALATED = "escalated"
    FAILED = "failed"
    CANCELED = "canceled"
    STALE = "stale"


#: States from which a task never advances again.
TERMINAL_STATES: frozenset[TaskState] = frozenset(
    {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED}
)

#: The non-linear states.
EXCEPTIONAL_STATES: frozenset[TaskState] = frozenset(
    {
        TaskState.BLOCKED,
        TaskState.REPAIR_REQUIRED,
        TaskState.REPLAN_REQUIRED,
        TaskState.ESCALATED,
        TaskState.FAILED,
        TaskState.CANCELED,
        TaskState.STALE,
    }
)


class ArtifactType(str, enum.Enum):
    """The ``artifact_type`` discriminator value of each schema."""

    WORK_PACKAGE = "work-package"
    TASK_CONTRACT = "task-contract"
    CONTEXT_MANIFEST = "context-manifest"
    IMPLEMENTATION_MANIFEST = "implementation-manifest"
    REVIEW_VERDICT = "review-verdict"
    INTEGRATION_MANIFEST = "integration-manifest"
    HUMAN_APPROVAL = "human-approval"
    PHASE_GATE = "phase-gate"
    EVENT = "event"
    ARCHITECTURE_AUDIT = "architecture-audit"


_T = TypeVar("_T", bound="Artifact")


@dataclass(frozen=True)
class Artifact:
    """Base for every modelled artifact. Subclasses set ``ARTIFACT_TYPE``."""

    ARTIFACT_TYPE: ClassVar[ArtifactType]

    @classmethod
    def from_mapping(cls: type[_T], data: Mapping[str, Any]) -> _T:
        """Build from a validated artifact dict, taking only modelled fields.

        Raises ``ValueError`` if ``artifact_type`` is present and disagrees with
        the class, or if a modelled required field is missing.
        """
        declared = data.get("artifact_type")
        if declared is not None and declared != cls.ARTIFACT_TYPE.value:
            raise ValueError(
                f"{cls.__name__} expects artifact_type "
                f"{cls.ARTIFACT_TYPE.value!r}, got {declared!r}"
            )
        names = {f.name for f in fields(cls)}
        missing = [n for n in names if n not in data]
        if missing:
            raise ValueError(f"{cls.__name__} missing fields: {sorted(missing)}")
        return cls(**{n: data[n] for n in names})  # type: ignore[arg-type]


@dataclass(frozen=True)
class WorkPackage(Artifact):
    ARTIFACT_TYPE: ClassVar[ArtifactType] = ArtifactType.WORK_PACKAGE
    work_package_id: str
    phase_id: str
    title: str
    objective: str
    status: str
    initial_capability_class: str
    owning_planner_role: str
    bootstrap_phase: bool
    created_at: str
    plan_hash: str


@dataclass(frozen=True)
class TaskContract(Artifact):
    ARTIFACT_TYPE: ClassVar[ArtifactType] = ArtifactType.TASK_CONTRACT
    task_id: str
    work_package_id: str
    contract_version: str
    contract_hash: str
    base_commit: str
    execution_mode: str
    status: str
    created_at: str
    expires_at: str


@dataclass(frozen=True)
class ContextManifest(Artifact):
    ARTIFACT_TYPE: ClassVar[ArtifactType] = ArtifactType.CONTEXT_MANIFEST
    context_hash: str
    task_id: str
    attempt_id: str
    compiled_at: str
    role: str


@dataclass(frozen=True)
class ImplementationManifest(Artifact):
    ARTIFACT_TYPE: ClassVar[ArtifactType] = ArtifactType.IMPLEMENTATION_MANIFEST
    task_id: str
    attempt_id: str
    lease_id: str
    lease_epoch: int
    fencing_token: int
    worker_role: str
    provider: str
    model: str
    contract_hash: str
    context_hash: str
    base_commit: str
    result_commit: str
    termination_reason: str


@dataclass(frozen=True)
class ReviewVerdict(Artifact):
    ARTIFACT_TYPE: ClassVar[ArtifactType] = ArtifactType.REVIEW_VERDICT
    review_id: str
    task_id: str
    attempt_id: str
    reviewer_role: str
    reviewer_session_id: str
    reviewer_run_id: str
    reviewed_result_commit: str
    contract_hash: str
    context_hash: str
    verdict: str


@dataclass(frozen=True)
class IntegrationManifest(Artifact):
    ARTIFACT_TYPE: ClassVar[ArtifactType] = ArtifactType.INTEGRATION_MANIFEST
    integration_id: str
    task_id: str
    integrator_role: str
    integrator_session_id: str
    candidate_result_commit: str
    reviewed_diff_hash: str
    review_id: str
    integration_base_commit: str
    integration_commit: str
    merge_strategy: str
    candidate_patch_unchanged: bool
    completed_at: str


@dataclass(frozen=True)
class HumanApproval(Artifact):
    ARTIFACT_TYPE: ClassVar[ArtifactType] = ArtifactType.HUMAN_APPROVAL
    approval_id: str
    schema_version: str
    approver_principal: str
    machine_identity: str
    authentication_method: str
    authenticated_at: str
    authorized_action: str
    target_type: str
    target_id: str
    target_hash: str
    issued_at: str
    expires_at: str
    single_use: bool
    nonce: str
    control_plane_seal: str


@dataclass(frozen=True)
class PhaseGate(Artifact):
    ARTIFACT_TYPE: ClassVar[ArtifactType] = ArtifactType.PHASE_GATE
    gate_id: str
    phase_id: str
    evaluated_at: str
    evaluated_against_commit: str
    gate_result: str


@dataclass(frozen=True)
class Event(Artifact):
    ARTIFACT_TYPE: ClassVar[ArtifactType] = ArtifactType.EVENT
    event_id: str
    trace_id: str
    occurred_at: str
    actor_role: str
    reason_code: str
    event_type: str


@dataclass(frozen=True)
class ArchitectureAudit(Artifact):
    ARTIFACT_TYPE: ClassVar[ArtifactType] = ArtifactType.ARCHITECTURE_AUDIT
    audit_id: str
    gate_id: str
    pass_id: str
    auditor_role: str
    auditor_session_id: str
    auditor_run_id: str
    audited_commit: str
    audited_tree_hash: str
    verdict: str


#: ``artifact_type`` value -> modelled dataclass.
MODEL_BY_ARTIFACT_TYPE: dict[str, type[Artifact]] = {
    cls.ARTIFACT_TYPE.value: cls
    for cls in (
        WorkPackage,
        TaskContract,
        ContextManifest,
        ImplementationManifest,
        ReviewVerdict,
        IntegrationManifest,
        HumanApproval,
        PhaseGate,
        Event,
        ArchitectureAudit,
    )
}
