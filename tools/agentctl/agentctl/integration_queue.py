"""Deterministic integration queue and serialization claim (gates: ``gate_runner``).

The queue is a pure function of stored facts: tasks in ``integration_ready``,
ordered by ``(integration_order, topological index, task_id)``. Neither a model
nor the caller can change that order. A candidate is *eligible* only when every
required dependency is ``completed`` and it is not parked behind an unchanged
dynamic rejection (a retry with no new evidence is forbidden by AGENTS.md s9).

State is kept in the existing ``tasks``, ``artifacts`` and ``events`` tables and
changed only through ``EventStore.execute`` and ``scheduler.transition``. New
artifact types: ``integration-lease``, ``integration-intent``,
``integration-rejection``, ``integration-reconciliation``, ``integration-record``.

Serialization is enforced inside the claim transaction (at most one task in
``integrating``), not by the process-level lock that ``integrate`` also takes.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import secrets
import sqlite3
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .dag import DependencyGraph
from .errors import AgentctlError, IllegalTransition
from .events import make_event
from .gate_runner import run_checks, scrubbed_environment
from .hashing import sha256_of_json
from .models import TaskState
from .scheduler import transition
from .store import EventStore

__all__ = [
    "DEPENDENCY_CANCELED",
    "QUEUE_INELIGIBLE_STATES",
    "IntegrationBusy",
    "IntegrationQueue",
    "IntegrationRefused",
    "IntegrationRejected",
    "QueueEntry",
    "build_queue",
    "claim_integration",
    "complete_integration",
    "current_lease",
    "renew_lease",
    "connection_query",
    "lease_is_live",
    "latest_intent",
    "new_lease",
    "next_lease_epoch",
    "PROPOSED_ACTIONS",
    "record_reconciliation",
    "record_rejection",
    "run_gate_commands",
    "scrubbed_environment",
    "store_query",
]

INTEGRATION_LEASE_SECONDS = 1800
_SENTINEL_ORDER = 1 << 62

#: States whose tasks are never queue entries, however many rejection artifacts they carry. The queue
#: admits only ``integration_ready``; this set names what that exclusion means for the lifecycle
#: commands: a canceled (superseded) task must not be retried each time the integration head moves,
#: and stale/blocked/replan/repair/escalated tasks must first return to ``integration_ready``
#: through review and verification.
QUEUE_INELIGIBLE_STATES = frozenset(
    state.value
    for state in (
        TaskState.CANCELED,
        TaskState.STALE,
        TaskState.BLOCKED,
        TaskState.REPLAN_REQUIRED,
        TaskState.REPAIR_REQUIRED,
        TaskState.ESCALATED,
        TaskState.FAILED,
    )
)

#: Parked reason for a candidate whose dependency was canceled: it can never become eligible by
#: waiting, so retrying it on every head move would only write rejection artifacts.
DEPENDENCY_CANCELED = "dependency_canceled"


class IntegrationRefused(AgentctlError):
    """A request was refused without a judgement about the candidate."""

    code = "integration_refused"

    def __init__(self, message: str, *, reason_code: str, **context: object) -> None:
        super().__init__(message, reason_code=reason_code, **context)
        self.reason_code = reason_code


class IntegrationBusy(IntegrationRefused):
    """Another task already holds the single integration slot."""

    code = "integration_busy"


class IntegrationRejected(IntegrationRefused):
    """A candidate failed a pre-integration verification or an integration gate.

    ``state_event``/``state_target`` name an existing legal transition to apply
    with the rejection; ``None`` means the task state is left unchanged because
    no legal transition expresses the outcome.
    """

    code = "integration_rejected"

    def __init__(
        self,
        message: str,
        *,
        reason_code: str,
        outcome: str,
        state_event: str | None = None,
        state_target: TaskState | None = None,
        retry_requires_new_evidence: bool = False,
        **context: object,
    ) -> None:
        super().__init__(message, reason_code=reason_code, outcome=outcome, **context)
        self.outcome = outcome
        self.state_event = state_event
        self.state_target = state_target
        self.retry_requires_new_evidence = retry_requires_new_evidence
        self.details = dict(context)


#: Proposed (never auto-created) follow-up for each rejection outcome.
PROPOSED_ACTIONS = {
    "repair_required": "bounded integration-repair task with fresh verification and independent review",
    "replan_required": "bounded replan with renewed context and fresh verification and review",
    "review_required": "independent review bound to the current candidate evidence",
    "waiting_on_dependency": "integrate the required dependencies first; no repair needed",
    "human_required": "human or architectural authority must inspect the integration branch",
}


# ---------------------------------------------------------------- queue ----

Query = Callable[[str, Sequence[Any]], Sequence[Any]]


def store_query(store: EventStore) -> Query:
    return lambda sql, params=(): store.rows(sql, tuple(params))


def connection_query(connection: sqlite3.Connection) -> Query:
    return lambda sql, params=(): connection.execute(sql, tuple(params)).fetchall()


@dataclass(frozen=True)
class QueueEntry:
    task_id: str
    integration_order: int | None
    topological_index: int
    waiting_on: tuple[str, ...]
    parked_reason: str | None

    @property
    def eligible(self) -> bool:
        return not self.waiting_on and self.parked_reason is None

    @property
    def sort_key(self) -> tuple[int, int, str]:
        order = _SENTINEL_ORDER if self.integration_order is None else self.integration_order
        return (order, self.topological_index, self.task_id)


@dataclass(frozen=True)
class IntegrationQueue:
    entries: tuple[QueueEntry, ...]

    @property
    def eligible(self) -> tuple[QueueEntry, ...]:
        return tuple(entry for entry in self.entries if entry.eligible)

    @property
    def head(self) -> QueueEntry | None:
        eligible = self.eligible
        return eligible[0] if eligible else None

    def entry(self, task_id: str) -> QueueEntry | None:
        return next((entry for entry in self.entries if entry.task_id == task_id), None)


def _integration_order(payload: Mapping[str, Any]) -> int | None:
    value = payload.get("integration_order")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def build_queue(query: Query, branch_head: str | None) -> IntegrationQueue:
    """Compute the queue from stored facts; ``branch_head`` keys the parking rule."""
    tasks = {
        row["task_id"]: row
        for row in query("SELECT task_id, state, payload_json FROM tasks", ())
    }
    edges: dict[str, list[str]] = {task_id: [] for task_id in tasks}
    for row in query(
        "SELECT task_id, depends_on_task_id FROM dependencies WHERE depends_on_task_id != ''",
        (),
    ):
        edges.setdefault(row["task_id"], []).append(row["depends_on_task_id"])
    topological = {
        node: index for index, node in enumerate(DependencyGraph(edges).topological_order())
    }
    entries: list[QueueEntry] = []
    for task_id, row in tasks.items():
        if row["state"] in QUEUE_INELIGIBLE_STATES or row["state"] != TaskState.INTEGRATION_READY.value:
            continue
        waiting = tuple(
            sorted(
                dependency
                for dependency in edges.get(task_id, ())
                if dependency not in tasks
                or tasks[dependency]["state"] != TaskState.COMPLETED.value
            )
        )
        entries.append(
            QueueEntry(
                task_id=task_id,
                integration_order=_integration_order(json.loads(row["payload_json"])),
                topological_index=topological.get(task_id, len(topological)),
                waiting_on=waiting,
                parked_reason=(
                    DEPENDENCY_CANCELED
                    if any(
                        dependency in tasks and tasks[dependency]["state"] == TaskState.CANCELED.value
                        for dependency in edges.get(task_id, ())
                    )
                    else _parked_reason(query, task_id, branch_head)
                ),
            )
        )
    return IntegrationQueue(tuple(sorted(entries, key=lambda entry: entry.sort_key)))


def _parked_reason(query: Query, task_id: str, branch_head: str | None) -> str | None:
    attempt = query(
        "SELECT attempt_id FROM attempts WHERE task_id = ? ORDER BY attempt_number DESC LIMIT 1",
        (task_id,),
    )
    if not attempt:
        return None
    rows = query(
        "SELECT payload_json FROM artifacts WHERE artifact_type = 'integration-rejection' "
        "AND task_id = ? AND attempt_id = ? ORDER BY rowid DESC LIMIT 1",
        (task_id, attempt[0]["attempt_id"]),
    )
    if not rows:
        return None
    payload = json.loads(rows[0]["payload_json"])
    if payload.get("retry_requires_new_evidence") is True and payload.get(
        "integration_head"
    ) == branch_head:
        return str(payload.get("reason_code"))
    return None


# ------------------------------------------------------- artifact lookups ---


def latest_intent(query: Query, task_id: str) -> dict[str, Any] | None:
    rows = query(
        "SELECT payload_json FROM artifacts WHERE artifact_type = 'integration-intent' "
        "AND task_id = ? AND integrable = 1 ORDER BY rowid DESC LIMIT 1",
        (task_id,),
    )
    return json.loads(rows[0]["payload_json"]) if rows else None


def next_lease_epoch(query: Query, task_id: str) -> int:
    rows = query(
        "SELECT MAX(CAST(json_extract(payload_json, '$.lease_epoch') AS INTEGER)) AS epoch "
        "FROM artifacts WHERE task_id = ? AND artifact_type = 'integration-lease'",
        (task_id,),
    )
    return int((rows[0]["epoch"] if rows else None) or 0) + 1


def _timestamp(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def new_lease(task_id: str, integration_id: str, epoch: int, integrator_id: str) -> dict[str, Any]:
    """Mint an ``integration-lease`` payload, mirroring ``review-assignment``."""
    return {
        "task_id": task_id,
        "integration_id": integration_id,
        "lease_id": str(uuid.uuid4()),
        "lease_epoch": epoch,
        "fencing_token": secrets.token_hex(32),
        "integrator_role": "integrator",
        "integrator_run_id": integrator_id,
        "expires_at": _timestamp(
            dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=INTEGRATION_LEASE_SECONDS)
        ),
    }


def lease_is_live(lease: Mapping[str, Any]) -> bool:
    try:
        expires = dt.datetime.fromisoformat(str(lease["expires_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return False
    return expires > dt.datetime.now(dt.timezone.utc)


def _commits(*values: str | None) -> list[str]:
    return [value for value in values if isinstance(value, str) and len(value) == 40]


# --------------------------------------------------- state transitions ------


def claim_integration(
    store: EventStore,
    *,
    intent: Mapping[str, Any],
    lease: Mapping[str, Any],
    branch_head: Callable[[], str | None],
) -> dict[str, Any]:
    """Transaction 1: ``integration_ready -> integrating`` with the intent on record.

    Inside one ``BEGIN IMMEDIATE`` transaction this re-checks that the task is
    still ``integration_ready``, that no other task is ``integrating``, that the
    task is still the queue head, and that the integration branch still sits on
    the head the plan was computed against. Only then are the lease and intent
    artifacts written together with the legal ``integration_lease_issued``
    transition.
    """
    task_id = str(intent["task_id"])
    integration_id = str(intent["integration_id"])
    lease_artifact = f"integration-lease:{task_id}:{lease['lease_id']}"
    intent_artifact = f"integration-intent:{integration_id}"
    event = make_event(
        event_type="integration_lease_issued",
        reason_code="integration_lease_issued",
        actor_role="integrator",
        task_id=task_id,
        attempt_id=str(intent["attempt_id"]),
        prior_state=TaskState.INTEGRATION_READY.value,
        next_state=TaskState.INTEGRATING.value,
        repository_commits=_commits(
            intent["expected_old_head"], intent["candidate_commit"], intent["planned_commit"]
        ),
        artifact_refs=[intent_artifact, lease_artifact],
    )

    def mutation(connection: sqlite3.Connection) -> dict[str, Any]:
        current = connection.execute(
            "SELECT state FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if current is None or current["state"] != TaskState.INTEGRATION_READY.value:
            raise IntegrationRefused(
                "task is no longer integration_ready",
                reason_code="task_not_integration_ready",
                task_id=task_id,
                state=None if current is None else current["state"],
            )
        busy = connection.execute(
            "SELECT task_id FROM tasks WHERE state = 'integrating' ORDER BY task_id LIMIT 1"
        ).fetchone()
        if busy is not None:
            raise IntegrationBusy(
                "another integration is in progress",
                reason_code="integration_in_progress",
                task_id=task_id,
                integrating=busy["task_id"],
            )
        actual_head = branch_head()
        queue = build_queue(connection_query(connection), actual_head)
        head = queue.head
        if head is None or head.task_id != task_id:
            raise IntegrationRefused(
                "task is not the queue head",
                reason_code="not_queue_head",
                task_id=task_id,
                head=None if head is None else head.task_id,
            )
        if actual_head != intent["branch_head_before"]:
            raise IntegrationRefused(
                "integration branch moved after planning",
                reason_code="branch_moved_after_planning",
                task_id=task_id,
                expected=intent["branch_head_before"],
                actual=actual_head,
            )
        transition(current["state"], "integration_lease_issued", TaskState.INTEGRATING)
        connection.execute(
            "UPDATE tasks SET state = 'integrating', updated_at = CURRENT_TIMESTAMP "
            "WHERE task_id = ?",
            (task_id,),
        )
        connection.execute(
            "INSERT INTO artifacts(artifact_id, artifact_type, task_id, attempt_id, "
            "content_hash, path, integrable, payload_json) VALUES "
            "(?, 'integration-lease', ?, ?, ?, '', 1, ?)",
            (
                lease_artifact,
                task_id,
                intent["attempt_id"],
                lease["fencing_token"],
                json.dumps(dict(lease), sort_keys=True, separators=(",", ":")),
            ),
        )
        connection.execute(
            "INSERT INTO artifacts(artifact_id, artifact_type, task_id, attempt_id, "
            "content_hash, path, integrable, payload_json) VALUES "
            "(?, 'integration-intent', ?, ?, ?, '', 1, ?)",
            (
                intent_artifact,
                task_id,
                intent["attempt_id"],
                sha256_of_json(dict(intent)),
                json.dumps(dict(intent), sort_keys=True, separators=(",", ":")),
            ),
        )
        return {"task_id": task_id, "state": "integrating", "integration_id": integration_id}

    return dict(store.execute(f"integration-claim:{integration_id}", event, mutation))



def current_lease(query: Query, task_id: str) -> dict[str, Any] | None:
    rows = query(
        "SELECT payload_json FROM artifacts WHERE artifact_type = 'integration-lease' "
        "AND task_id = ? AND integrable = 1 ORDER BY rowid DESC LIMIT 1",
        (task_id,),
    )
    return json.loads(rows[0]["payload_json"]) if rows else None


def renew_lease(
    store: EventStore, *, intent: Mapping[str, Any], lease: Mapping[str, Any]
) -> dict[str, Any]:
    """Replace an expired integration lease with a new epoch and fencing token.

    The old lease artifact is retired (``integrable = 0``) in the same transaction
    that records the renewal, so a stale token can never complete the integration.
    """
    task_id = str(intent["task_id"])
    artifact_id = f"integration-lease:{task_id}:{lease['lease_id']}"
    event = make_event(
        event_type="integration_lease_renewed",
        reason_code="integration_lease_expired",
        actor_role="integrator",
        task_id=task_id,
        attempt_id=str(intent["attempt_id"]),
        prior_state=TaskState.INTEGRATING.value,
        next_state=TaskState.INTEGRATING.value,
        artifact_refs=[artifact_id, f"intent:{intent['integration_id']}"],
    )

    def mutation(connection: sqlite3.Connection) -> dict[str, Any]:
        intent_row = connection.execute(
            "SELECT integrable FROM artifacts WHERE artifact_id = ?",
            (f"integration-intent:{intent['integration_id']}",),
        ).fetchone()
        state = connection.execute(
            "SELECT state FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if (
            intent_row is None
            or intent_row["integrable"] != 1
            or state is None
            or state["state"] != TaskState.INTEGRATING.value
        ):
            raise IntegrationRefused(
                "lease can be renewed only for an integrating task with a live intent",
                reason_code="reconciliation_required",
                task_id=task_id,
            )
        connection.execute(
            "UPDATE artifacts SET integrable = 0 WHERE task_id = ? "
            "AND artifact_type = 'integration-lease'",
            (task_id,),
        )
        connection.execute(
            "INSERT INTO artifacts(artifact_id, artifact_type, task_id, attempt_id, "
            "content_hash, path, integrable, payload_json) VALUES "
            "(?, 'integration-lease', ?, ?, ?, '', 1, ?)",
            (
                artifact_id,
                task_id,
                intent["attempt_id"],
                lease["fencing_token"],
                json.dumps(dict(lease), sort_keys=True, separators=(",", ":")),
            ),
        )
        return {"task_id": task_id, "lease_epoch": lease["lease_epoch"]}

    return dict(store.execute(f"integration-lease-renew:{lease['lease_id']}", event, mutation))


def complete_integration(
    store: EventStore, *, intent: Mapping[str, Any], manifest: Mapping[str, Any]
) -> dict[str, Any]:
    """Transaction 2: ``integrating -> completed`` plus the ``integration-record``.

    Idempotent per integration id: replaying it returns the first result and
    never writes a second record.
    """
    task_id = str(intent["task_id"])
    integration_id = str(intent["integration_id"])
    record_id = f"integration-record:{task_id}"
    event = make_event(
        event_type="integration_passed",
        reason_code="integration_passed",
        actor_role="integrator",
        task_id=task_id,
        attempt_id=str(intent["attempt_id"]),
        prior_state=TaskState.INTEGRATING.value,
        next_state=TaskState.COMPLETED.value,
        repository_commits=_commits(
            manifest["integration_base_commit"],
            manifest["candidate_result_commit"],
            manifest["integration_commit"],
        ),
        artifact_refs=[record_id, f"integration-intent:{integration_id}"],
    )

    def mutation(connection: sqlite3.Connection) -> dict[str, Any]:
        current = connection.execute(
            "SELECT state FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if current is None:
            raise IllegalTransition("task disappeared before integration", task_id=task_id)
        transition(current["state"], "integration_passed", TaskState.COMPLETED)
        for artifact_id, label in (
            (f"integration-intent:{integration_id}", "intent"),
            (f"integration-lease:{task_id}:{manifest['lease_id']}", "lease"),
        ):
            live = connection.execute(
                "SELECT integrable FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if live is None or live["integrable"] != 1:
                raise IntegrationRefused(
                    f"integration {label} is not integrable",
                    reason_code=f"{label}_not_integrable",
                    task_id=task_id,
                    artifact=artifact_id,
                )
        # Fencing: the manifest must carry exactly the current lease, and it must be live.
        current_row = connection.execute(
            "SELECT artifact_id, payload_json FROM artifacts WHERE artifact_type = "
            "'integration-lease' AND task_id = ? AND integrable = 1 ORDER BY rowid DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        current = json.loads(current_row["payload_json"]) if current_row else {}
        if (
            current_row is None
            or current_row["artifact_id"] != f"integration-lease:{task_id}:{manifest['lease_id']}"
            or current.get("lease_epoch") != manifest["lease_epoch"]
            or current.get("fencing_token") != manifest["fencing_token"]
            or current.get("integration_id") != integration_id
        ):
            raise IntegrationRefused(
                "manifest lease, epoch or fencing token is not the current integration lease",
                reason_code="lease_fencing_mismatch",
                task_id=task_id,
            )
        if not lease_is_live(current):
            raise IntegrationRefused(
                "integration lease has expired; renew it before completing",
                reason_code="lease_expired",
                task_id=task_id,
                expires_at=current.get("expires_at"),
            )
        connection.execute(
            "UPDATE tasks SET state = 'completed', updated_at = CURRENT_TIMESTAMP "
            "WHERE task_id = ?",
            (task_id,),
        )
        connection.execute(
            "UPDATE artifacts SET integrable = 0 WHERE task_id = ? "
            "AND artifact_type IN ('integration-intent', 'integration-lease')",
            (task_id,),
        )
        connection.execute(
            "INSERT INTO artifacts(artifact_id, artifact_type, task_id, attempt_id, "
            "content_hash, path, integrable, payload_json) VALUES "
            "(?, 'integration-record', ?, ?, ?, '', 1, ?)",
            (
                record_id,
                task_id,
                intent["attempt_id"],
                sha256_of_json(dict(manifest)),
                json.dumps(dict(manifest), sort_keys=True, separators=(",", ":")),
            ),
        )
        return {
            "task_id": task_id,
            "state": "completed",
            "integration_id": integration_id,
            "integration_commit": manifest["integration_commit"],
        }

    return dict(store.execute(f"integration-complete:{integration_id}", event, mutation))


def record_rejection(
    store: EventStore,
    *,
    task_id: str,
    attempt_id: str | None,
    state: str,
    rejection: IntegrationRejected,
    integration_head: str | None,
    integrator_id: str,
    commits: Sequence[str | None] = (),
) -> dict[str, Any]:
    """Record a non-advancing rejection: one event plus an ``integration-rejection``.

    Follows the ``review_rejected`` pattern. The task state changes only when the
    rejection names a legal transition (for example ``dependency_changed``).
    """
    artifact_id = f"integration-rejection:{task_id}:{uuid.uuid4()}"
    next_state = state
    event_type = "integration_rejected"
    if rejection.state_event is not None and rejection.state_target is not None:
        event_type = rejection.state_event
        next_state = rejection.state_target.value
    event = make_event(
        event_type=event_type,
        reason_code=rejection.reason_code,
        actor_role="integrator",
        task_id=task_id,
        attempt_id=attempt_id,
        prior_state=state,
        next_state=next_state,
        repository_commits=_commits(*commits),
        artifact_refs=[artifact_id, f"outcome:{rejection.outcome}"],
    )
    payload = {
        "task_id": task_id,
        "attempt_id": attempt_id,
        "reason_code": rejection.reason_code,
        "outcome": rejection.outcome,
        "proposed_follow_up": PROPOSED_ACTIONS.get(rejection.outcome, "inspect"),
        "follow_up_auto_created": False,
        "retry_requires_new_evidence": rejection.retry_requires_new_evidence,
        "integration_head": integration_head,
        "integrator_run_id": integrator_id,
        "state_before": state,
        "state_after": next_state,
        "details": _jsonable(rejection.details),
    }

    def mutation(connection: sqlite3.Connection) -> dict[str, Any]:
        current = connection.execute(
            "SELECT state FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if current is None or current["state"] != state:
            raise IllegalTransition("task changed before rejection was recorded", task_id=task_id)
        if next_state != state:
            transition(state, event_type, TaskState(next_state))
            connection.execute(
                "UPDATE tasks SET state = ?, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?",
                (next_state, task_id),
            )
        connection.execute(
            "INSERT INTO artifacts(artifact_id, artifact_type, task_id, attempt_id, "
            "content_hash, path, integrable, payload_json) VALUES "
            "(?, 'integration-rejection', ?, ?, ?, '', 0, ?)",
            (
                artifact_id,
                task_id,
                attempt_id,
                sha256_of_json(payload),
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
            ),
        )
        return {"artifact_id": artifact_id, "task_id": task_id, "state": next_state}

    return dict(store.execute(f"integration-rejected:{task_id}:{uuid.uuid4()}", event, mutation))


def record_reconciliation(
    store: EventStore,
    *,
    intent: Mapping[str, Any],
    finding: str,
    branch_head: str | None,
    actions: str,
) -> dict[str, Any]:
    """Record what reconciliation found in Git for a task stuck in ``integrating``."""
    task_id = str(intent["task_id"])
    artifact_id = f"integration-reconciliation:{task_id}:{uuid.uuid4()}"
    event = make_event(
        event_type="integration_reconciled",
        reason_code=finding,
        actor_role="integrator",
        task_id=task_id,
        attempt_id=str(intent["attempt_id"]),
        prior_state=TaskState.INTEGRATING.value,
        next_state=TaskState.INTEGRATING.value,
        repository_commits=_commits(
            intent["expected_old_head"], intent["planned_commit"], branch_head
        ),
        artifact_refs=[artifact_id, f"intent:{intent['integration_id']}"],
    )
    payload = {
        "task_id": task_id,
        "integration_id": intent["integration_id"],
        "finding": finding,
        "branch_head": branch_head,
        "expected_old_head": intent["expected_old_head"],
        "planned_commit": intent["planned_commit"],
        "action": actions,
    }

    def mutation(connection: sqlite3.Connection) -> dict[str, Any]:
        connection.execute(
            "INSERT INTO artifacts(artifact_id, artifact_type, task_id, attempt_id, "
            "content_hash, path, integrable, payload_json) VALUES "
            "(?, 'integration-reconciliation', ?, ?, ?, '', 0, ?)",
            (
                artifact_id,
                task_id,
                intent["attempt_id"],
                sha256_of_json(payload),
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
            ),
        )
        return {"artifact_id": artifact_id, "finding": finding}

    return dict(store.execute(f"integration-reconcile:{task_id}:{uuid.uuid4()}", event, mutation))


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


# ------------------------------------------------------------ gate runner ---


def run_gate_commands(
    commands: Sequence[str], *, cwd: str | os.PathLike[str], timeout: float
) -> list[dict[str, Any]]:
    """Run each contract check in ``cwd``; delegates to :func:`gate_runner.run_checks`.

    One hard deadline per command; no descendant may outlive its gate; output is
    bounded; the environment is an allow-list. See ``agentctl.gate_runner``.
    """
    return run_checks(commands, cwd, timeout)
