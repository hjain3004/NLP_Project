"""Operator lifecycle: repair authorization, supersession, and cancellation.

Everything here changes task state only through ``EventStore.execute`` and
``scheduler.transition`` and only along rows that already exist in the pinned
transition table (design section 14.1). No state-machine row, schema, or policy is added.

Repair (``task repair``)
------------------------
``repair_required -> ready`` is the existing ``repair_authorized`` row. It is legal only
while the task still has attempts (the ceiling is never extended: ``dispatch`` consumes the
attempt, this module never touches a budget) and only against **new evidence**: a blocking
independent-review verdict, a failed deterministic-verification record, or an ended attempt,
all of the task's *latest* attempt. The authorization is a ``repair-authorization`` artifact
that records the evidence, a bounded operator note, the operator identity string
(self-declared and unauthenticated: this is not a human-approval record), the previous
attempt, and the feedback ``run`` will give the next author: the reviewer's blocking findings
or the failed-check summary, never the author's own claims. The same evidence is idempotent
(it returns the existing authorization); evidence already spent on an earlier attempt is
refused.

Cancellation and supersession (``task cancel`` / ``task supersede``)
--------------------------------------------------------------------
There is no direct ``-> canceled`` row for most states. The only legal route to ``canceled`` for an
operator is ``replan_required -> canceled`` (``replacement_plan_accepted``), so a task is walked
there along existing rows. The shortest chain is computed from the live table by breadth-first
search over the administrative events only (``dependency_changed``, ``context_rebuilt``,
``contract_invalid``, ``replacement_plan_accepted``); ``authority_decision`` (an architectural
authority's row) and the lease/verification/review rows are never used:

=============================================  =====  ===========================================================
source state                                   steps  chain
=============================================  =====  ===========================================================
draft, validated                               2      contract_invalid -> replan_required -> replacement_plan_accepted
ready, candidate, deterministic_verification,  4      dependency_changed -> stale -> context_rebuilt -> validated
independent_review, integration_ready,                -> contract_invalid -> replan_required
repair_required, blocked, escalated                   -> replacement_plan_accepted -> canceled
stale                                          3      context_rebuilt -> contract_invalid -> replacement_plan_accepted
replan_required                                1      replacement_plan_accepted
canceled                                       0      idempotent no-op
leased, running                                refused: an author lease is live; expire it first
integrating                                    refused: an integration is in flight on the Git branch
completed, failed                              refused: terminal
=============================================  =====  ===========================================================

The event names are the table's, not statements of fact about the task (no context was rebuilt):
every event of the chain carries the reason ``superseded_by:<new>`` (or ``operator_canceled``) and
references the ``task-cancellation`` decision artifact, so a reader can tell the chain from a
genuine interface change.

Each step is its own transaction and event; the first step also records the decision artifact. The
chain is **resumable**: every iteration re-plans from the live state, steps are idempotent per
``(task, event, from_state)``, and a decision that conflicts with the recorded one is refused. A
first step that loses a race with a different writer is refused rather than re-planned, so a
concurrent ``task repair`` and ``task supersede`` have exactly one winner. History is never
deleted: events, attempts, artifacts, reviews and model runs stay.

Dependents
----------
Every direct dependent of the canceled task is resolved so nothing is left looking ready on a
dependency that can never complete. With a replacement the dependency rows are re-pointed
old -> new and a nonterminal dependent goes ``stale`` (``dependency_changed``); without one it goes
``blocked`` (``dependency_unavailable``) and its rows keep naming the canceled task. Terminal
dependents are left alone. Dependents that hold a live lease or are integrating are refused up
front.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import sqlite3
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import datetime as dt

from .errors import AgentctlError, IllegalTransition
from .events import InvalidEvent, make_event
from .hashing import sha256_hex, sha256_of_json
from .models import TERMINAL_STATES, TaskState
from .scheduler import transition
from .store import EventStore

__all__ = [
    "LifecycleRefused",
    "Step",
    "authorize_repair",
    "cancel_task",
    "latest_repair_authorization",
    "plan_cancel_chain",
    "repair_authorizations",
    "resolve_operator",
    "status_chain",
    "supersede_task",
]

NOTE_LIMIT = 1000
OPERATOR_LIMIT = 120
FEEDBACK_ITEMS = 20
FEEDBACK_FIELD_LIMIT = 2000
OPERATOR_ROLE = "operator"

#: Events the operator may use to reach ``canceled``. Deliberately excludes ``authority_decision``.
_ADMIN_EVENTS = ("dependency_changed", "context_rebuilt", "contract_invalid", "replacement_plan_accepted")

_EVIDENCE_REF = re.compile(r"[A-Za-z0-9._:/@-]{1,200}")
_HASH64 = re.compile(r"^[0-9a-f]{64}$")

_LIVE_LEASE_STATES = frozenset({TaskState.LEASED.value, TaskState.RUNNING.value})
_REFUSED_WHY = {
    TaskState.LEASED.value: (
        "an author attempt holds a live lease; canceling under it would orphan a running provider "
        "call. Let the run finish or expire the lease (the existing lease_expired path returns the "
        "task to ready), then cancel."
    ),
    TaskState.RUNNING.value: (
        "an author attempt is running under a live lease; canceling under it would orphan a running "
        "provider call. Let the run finish or expire the lease (the existing lease_expired path "
        "returns the task to ready), then cancel."
    ),
    TaskState.INTEGRATING.value: (
        "an integration is in flight and the integration branch may advance; let `agentctl integrate` "
        "finish or reconcile it first. Canceling now could leave Git and the store disagreeing."
    ),
    TaskState.COMPLETED.value: (
        "the task is completed (its candidate is integrated); a terminal task cannot be canceled. "
        "Reverting integrated work is a new task."
    ),
    TaskState.FAILED.value: "the task is failed, a terminal state; it cannot be canceled or superseded.",
}


class LifecycleRefused(AgentctlError):
    """A lifecycle command was refused; nothing changed (or, for a resumed chain, nothing more changed)."""

    code = "lifecycle_refused"

    def __init__(self, message: str, *, reason_code: str, **context: object) -> None:
        super().__init__(message, reason_code=reason_code, **context)
        self.reason_code = reason_code


class _StepRaced(Exception):
    """The task's state changed between planning and the step's transaction."""


@dataclass(frozen=True)
class Step:
    event: str
    from_state: str
    to_state: str

    def to_payload(self) -> dict[str, str]:
        return {"event": self.event, "from": self.from_state, "to": self.to_state}


# --------------------------------------------------------------------------- #
# Small helpers                                                               #
# --------------------------------------------------------------------------- #


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _clean(value: object, limit: int) -> str:
    text = "".join(ch for ch in str(value) if ch == "\n" or ch == "\t" or ch >= " ")
    return text.strip()[:limit]


def _bounded_text(value: object, name: str, limit: int = NOTE_LIMIT) -> str:
    text = _clean(value, limit + 1)
    if not text:
        raise LifecycleRefused(f"{name} is required and must not be blank", reason_code=f"{name}_required")
    if len(text) > limit:
        raise LifecycleRefused(
            f"{name} is longer than {limit} characters; summarize it and cite evidence by reference",
            reason_code=f"{name}_too_long",
        )
    return text


def resolve_operator(explicit: str | None = None) -> str:
    """The operator identity string: ``--operator``, ``AGENTCTL_OPERATOR``, else the local user.

    It is self-declared and unauthenticated. It identifies who typed the command in the record; it is
    not a human-approval record (AGENTS.md section 10) and authorizes nothing by itself.
    """
    value = explicit or os.environ.get("AGENTCTL_OPERATOR")
    if not value:
        try:
            value = f"local:{getpass.getuser()}"
        except Exception:  # no passwd entry in some containers
            value = "local:unknown"
    return _clean(value, OPERATOR_LIMIT) or "local:unknown"


def _valid_hash(value: object) -> str | None:
    return value if isinstance(value, str) and _HASH64.match(value) else None


def _task(store: EventStore, task_id: str) -> sqlite3.Row:
    rows = store.rows("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
    if not rows:
        raise LifecycleRefused("unknown task", reason_code="unknown_task", task_id=task_id)
    return rows[0]


def _latest_attempt(store: EventStore, task_id: str) -> sqlite3.Row | None:
    rows = store.rows(
        "SELECT * FROM attempts WHERE task_id = ? ORDER BY attempt_number DESC LIMIT 1", (task_id,)
    )
    return rows[0] if rows else None


def _attempts_remaining(store: EventStore, task_id: str) -> int:
    return int(
        store.scalar(
            "SELECT attempts_remaining FROM budgets WHERE scope_type = 'task' AND scope_id = ?",
            (task_id,),
        )
        or 0
    )


def _execute(store: EventStore, key: str, event: Mapping[str, Any], mutation: Callable[[sqlite3.Connection], Any]) -> Any:
    try:
        return store.execute(key, event, mutation)
    except InvalidEvent as exc:
        if "idempotency key was reused" in exc.message:
            raise LifecycleRefused(
                "a different request already used this operation key: another operator, decision or "
                "state recorded it first",
                reason_code="conflicting_request",
            ) from exc
        raise


# --------------------------------------------------------------------------- #
# Repair authorization                                                        #
# --------------------------------------------------------------------------- #


def repair_authorization_id(task_id: str, evidence: str) -> str:
    return f"repair-authorization:{task_id}:{sha256_hex(evidence.encode('utf-8'))[:16]}"


def repair_authorizations(store: EventStore, task_id: str) -> list[dict[str, Any]]:
    """Every repair authorization of ``task_id`` in the order they were made."""
    return [
        json.loads(row["payload_json"])
        for row in store.rows(
            "SELECT payload_json FROM artifacts WHERE task_id = ? AND artifact_type = 'repair-authorization' "
            "ORDER BY rowid",
            (task_id,),
        )
    ]


def latest_repair_authorization(store: EventStore, task_id: str) -> dict[str, Any] | None:
    found = repair_authorizations(store, task_id)
    return found[-1] if found else None


def _trim_findings(findings: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    kept = []
    for finding in findings[:FEEDBACK_ITEMS]:
        kept.append({key: _clean(value, FEEDBACK_FIELD_LIMIT) if isinstance(value, str) else value
                     for key, value in finding.items()})
    return kept


def _resolve_repair_evidence(store: EventStore, task_id: str, evidence: str, attempt_id: str) -> dict[str, Any]:
    """Read-only: what ``evidence`` is, that it is about the failed latest attempt, and its feedback."""
    review = store.rows("SELECT * FROM reviews WHERE review_id = ?", (evidence,))
    if review:
        row = review[0]
        verdict = json.loads(row["payload_json"])
        if row["task_id"] != task_id or row["attempt_id"] != attempt_id:
            raise LifecycleRefused(
                "that review is not about this task's latest attempt", reason_code="evidence_wrong_attempt",
                evidence=evidence,
            )
        findings = [f for f in verdict.get("findings", ()) if isinstance(f, Mapping)]
        blocking = [f for f in findings if f.get("blocking") is True]
        if verdict.get("verdict") == "no_blocking_findings" or not blocking:
            raise LifecycleRefused(
                "that review has no blocking finding; it cannot justify a repair",
                reason_code="evidence_not_blocking", evidence=evidence,
            )
        return {
            "kind": "review",
            "ref": evidence,
            "content_hash": sha256_of_json(verdict),
            "feedback": {
                "source": "independent_review",
                "review_id": evidence,
                "verdict": verdict.get("verdict"),
                "blocking_findings": _trim_findings(blocking),
            },
        }

    artifact = store.rows("SELECT * FROM artifacts WHERE artifact_id = ?", (evidence,))
    if artifact and artifact[0]["artifact_type"] == "verification-evidence":
        row = artifact[0]
        payload = json.loads(row["payload_json"])
        if row["task_id"] != task_id or row["attempt_id"] != attempt_id:
            raise LifecycleRefused(
                "that verification record is not about this task's latest attempt",
                reason_code="evidence_wrong_attempt", evidence=evidence,
            )
        if payload.get("passed") is True:
            raise LifecycleRefused(
                "that verification record passed; it cannot justify a repair",
                reason_code="evidence_not_failing", evidence=evidence,
            )
        failed = [
            {
                "command": _clean(check.get("command", ""), FEEDBACK_FIELD_LIMIT),
                "exit_code": check.get("exit_code"),
                "timed_out": bool(check.get("timed_out", False)),
                "reason": check.get("reason"),
            }
            for check in payload.get("checks", ())
            if check.get("exit_code") != 0 or check.get("reason")
        ]
        return {
            "kind": "verification",
            "ref": evidence,
            "content_hash": str(row["content_hash"]),
            "feedback": {
                "source": "deterministic_verification",
                "artifact_id": evidence,
                "evidence_hash": str(row["content_hash"]),
                "failure_reason": payload.get("failure_reason"),
                "repository_tamper": bool(payload.get("repository_tamper")),
                "failed_checks": failed[:FEEDBACK_ITEMS],
            },
        }

    if evidence.startswith("attempt:"):
        wanted = evidence.removeprefix("attempt:")
        rows = store.rows("SELECT * FROM attempts WHERE attempt_id = ? AND task_id = ?", (wanted, task_id))
        if rows and rows[0]["attempt_id"] != attempt_id:
            raise LifecycleRefused(
                "that attempt is not the task's latest attempt", reason_code="evidence_wrong_attempt",
                evidence=evidence,
            )
        if rows and rows[0]["state"] in ("failed", "revoked"):
            events = store.rows(
                "SELECT event_type, reason_code FROM events WHERE task_id = ? AND attempt_id = ? "
                "AND event_type IN ('submission_failed', 'lease_revoked') ORDER BY sequence DESC LIMIT 1",
                (task_id, wanted),
            )
            reason = events[0]["reason_code"] if events else "unknown"
            summary = {"source": "attempt_failure", "attempt_id": wanted, "failure_reason": reason,
                       "attempt_state": rows[0]["state"]}
            return {"kind": "attempt_failure", "ref": evidence, "content_hash": sha256_of_json(summary),
                    "feedback": summary}

    raise LifecycleRefused(
        "evidence is not a blocking review id, a failed verification artifact id, or `attempt:<id>` of an "
        "ended attempt of this task",
        reason_code="evidence_unknown", evidence=evidence,
    )


def authorize_repair(
    store: EventStore,
    task_id: str,
    *,
    evidence: str,
    note: str,
    operator: str | None = None,
) -> dict[str, Any]:
    """``repair_required -> ready`` against new evidence. See the module docstring."""
    evidence = str(evidence).strip()
    if not _EVIDENCE_REF.fullmatch(evidence):
        raise LifecycleRefused(
            "--evidence must be a review id, an artifact id, or attempt:<id> (no spaces)",
            reason_code="evidence_malformed",
        )
    note = _bounded_text(note, "note")
    operator = resolve_operator(operator)
    task = _task(store, task_id)
    authorization_id = repair_authorization_id(task_id, evidence)
    latest = _latest_attempt(store, task_id)

    def replay() -> dict[str, Any] | None:
        """The existing authorization for this evidence, as an idempotent reply (or a refusal if it is spent)."""
        existing = store.rows("SELECT payload_json FROM artifacts WHERE artifact_id = ?", (authorization_id,))
        if not existing:
            return None
        prior = json.loads(existing[0]["payload_json"])
        newest = _latest_attempt(store, task_id)
        if newest is not None and prior.get("previous_attempt_id") == newest["attempt_id"]:
            return {**prior, "outcome": "already_authorized", "state": str(_task(store, task_id)["state"])}
        raise LifecycleRefused(
            "this evidence was already spent on an earlier repair authorization; a repair needs NEW "
            "evidence about the attempt that just failed",
            reason_code="evidence_already_used", evidence=evidence, authorization_id=authorization_id,
        )

    replayed = replay()
    if replayed is not None:
        return replayed
    if task["state"] != TaskState.REPAIR_REQUIRED.value:
        raise LifecycleRefused(
            f"task is {task['state']!r}; a repair can be authorized only from 'repair_required'",
            reason_code="wrong_state", state=str(task["state"]),
        )
    remaining = _attempts_remaining(store, task_id)
    if remaining <= 0:
        raise LifecycleRefused(
            "no attempts remain; a repair cannot extend the task's attempt ceiling. Raising it needs a "
            "recorded budget decision (design section 14), and the next rung of the ladder is escalation",
            reason_code="attempts_exhausted", attempts_remaining=remaining,
        )
    if latest is None:
        raise LifecycleRefused("task has no attempt to repair", reason_code="no_attempt")
    resolved = _resolve_repair_evidence(store, task_id, evidence, str(latest["attempt_id"]))
    replayed = replay()  # another process may have authorized this very evidence while we were reading
    if replayed is not None:
        return replayed
    spent = [a for a in repair_authorizations(store, task_id) if a["evidence"]["content_hash"] == resolved["content_hash"]]
    if spent:
        raise LifecycleRefused(
            "this evidence content was already spent on an earlier repair authorization",
            reason_code="evidence_already_used", authorization_id=spent[0]["authorization_id"],
        )

    sequence = len(repair_authorizations(store, task_id)) + 1
    payload = {
        "artifact_type": "repair-authorization",
        "authorization_id": authorization_id,
        "task_id": task_id,
        "work_package_id": str(task["work_package_id"]),
        "sequence": sequence,
        "previous_attempt_id": str(latest["attempt_id"]),
        "previous_attempt_number": int(latest["attempt_number"]),
        "evidence": {"kind": resolved["kind"], "ref": resolved["ref"], "content_hash": resolved["content_hash"]},
        "note": note,
        "operator": operator,
        "operator_identity_authenticated": False,
        "authorized_at": _now(),
        "attempts_remaining_at_authorization": remaining,
        "feedback": resolved["feedback"],
    }
    event = make_event(
        event_type="repair_authorized",
        reason_code="repair_authorized",
        actor_role=OPERATOR_ROLE,
        task_id=task_id,
        attempt_id=str(latest["attempt_id"]),
        work_package_id=str(task["work_package_id"]),
        prior_state=TaskState.REPAIR_REQUIRED.value,
        next_state=TaskState.READY.value,
        contract_hash=_valid_hash(task["contract_hash"]),
        context_hash=_valid_hash(task["context_hash"]),
        artifact_refs=[authorization_id, evidence],
    )
    claim = str(uuid.uuid4())

    def mutation(connection: sqlite3.Connection) -> dict[str, Any]:
        current = connection.execute("SELECT state FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if current is None or current["state"] != TaskState.REPAIR_REQUIRED.value:
            raise LifecycleRefused(
                f"task is {None if current is None else current['state']!r}; it changed before the "
                "repair was recorded",
                reason_code="wrong_state",
            )
        budget = connection.execute(
            "SELECT attempts_remaining FROM budgets WHERE scope_type = 'task' AND scope_id = ?", (task_id,)
        ).fetchone()
        if budget is None or int(budget["attempts_remaining"] or 0) <= 0:
            raise LifecycleRefused("no attempts remain", reason_code="attempts_exhausted")
        newest = connection.execute(
            "SELECT attempt_id FROM attempts WHERE task_id = ? ORDER BY attempt_number DESC LIMIT 1", (task_id,)
        ).fetchone()
        if newest is None or newest["attempt_id"] != payload["previous_attempt_id"]:
            raise LifecycleRefused("a newer attempt exists", reason_code="evidence_wrong_attempt")
        transition(current["state"], "repair_authorized", TaskState.READY)
        connection.execute(
            "INSERT INTO artifacts(artifact_id, artifact_type, task_id, attempt_id, content_hash, path, "
            "integrable, payload_json) VALUES (?, 'repair-authorization', ?, ?, ?, '', 0, ?)",
            (
                authorization_id, task_id, payload["previous_attempt_id"], resolved["content_hash"],
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
            ),
        )
        connection.execute(
            "UPDATE tasks SET state = 'ready', updated_at = CURRENT_TIMESTAMP WHERE task_id = ?", (task_id,)
        )
        return {"claim": claim, "authorization": payload}

    try:
        result = _execute(store, f"repair-authorize:{task_id}:{sha256_hex(evidence.encode())}", event, mutation)
    except LifecycleRefused:
        replayed = replay()  # lost a race to an identical authorization: that is a replay, not a refusal
        if replayed is not None:
            return replayed
        raise
    outcome = "authorized" if result["claim"] == claim else "already_authorized"
    return {**result["authorization"], "outcome": outcome, "state": "ready"}


# --------------------------------------------------------------------------- #
# Cancellation chain                                                          #
# --------------------------------------------------------------------------- #


def plan_cancel_chain(state: str) -> list[Step]:
    """Shortest legal chain to ``canceled`` from ``state`` over the administrative rows, or a refusal."""
    if state == TaskState.CANCELED.value:
        return []
    if state in _REFUSED_WHY:
        raise LifecycleRefused(
            f"task is {state!r}: {_REFUSED_WHY[state]}", reason_code=f"state_{state}", state=state
        )
    try:
        start = TaskState(state)
    except ValueError:
        raise LifecycleRefused(f"unknown task state {state!r}", reason_code="unknown_state") from None

    def edges(current: TaskState) -> list[tuple[str, TaskState]]:
        found = []
        for event in _ADMIN_EVENTS:
            for desired in TaskState:
                try:
                    transition(current, event, desired)
                except IllegalTransition:
                    continue
                found.append((event, desired))
        return found

    queue: deque[tuple[TaskState, list[Step]]] = deque([(start, [])])
    seen = {start}
    while queue:
        current, path = queue.popleft()
        if current == TaskState.CANCELED:
            return path
        for event, nxt in edges(current):
            if nxt in seen:
                continue
            seen.add(nxt)
            queue.append((nxt, [*path, Step(event, current.value, nxt.value)]))
    raise LifecycleRefused(
        f"no legal chain from {state!r} to canceled exists in the transition table",
        reason_code="no_legal_chain", state=state,
    )


def _decision(store: EventStore, task_id: str) -> dict[str, Any] | None:
    rows = store.rows(
        "SELECT payload_json FROM artifacts WHERE artifact_id = ?", (f"task-cancellation:{task_id}",)
    )
    return json.loads(rows[0]["payload_json"]) if rows else None


def _dependents(store: EventStore, task_id: str) -> list[str]:
    return [
        row["task_id"]
        for row in store.rows(
            "SELECT DISTINCT task_id FROM dependencies WHERE depends_on_task_id = ? ORDER BY task_id", (task_id,)
        )
    ]


def _ancestors(store: EventStore, task_id: str) -> set[str]:
    edges: dict[str, list[str]] = {}
    for row in store.rows("SELECT task_id, depends_on_task_id FROM dependencies WHERE depends_on_task_id != ''"):
        edges.setdefault(row["task_id"], []).append(row["depends_on_task_id"])
    found: set[str] = set()
    pending = [task_id]
    while pending:
        for parent in edges.get(pending.pop(), ()):
            if parent not in found:
                found.add(parent)
                pending.append(parent)
    return found


def _check_replacement(store: EventStore, old: str, new: str, old_row: sqlite3.Row) -> None:
    if new == old:
        raise LifecycleRefused("a task cannot supersede itself", reason_code="replacement_is_self")
    rows = store.rows("SELECT state, work_package_id FROM tasks WHERE task_id = ?", (new,))
    if not rows:
        raise LifecycleRefused(
            "the replacement task does not exist; register it first", reason_code="replacement_unknown",
            replacement=new,
        )
    if rows[0]["work_package_id"] != old_row["work_package_id"]:
        raise LifecycleRefused(
            "the replacement belongs to another work package; supersession stays inside one package",
            reason_code="replacement_other_work_package", replacement=new,
        )
    if TaskState(rows[0]["state"]) in TERMINAL_STATES:
        raise LifecycleRefused(
            f"the replacement is {rows[0]['state']!r}, a terminal state; it cannot replace anything",
            reason_code="replacement_terminal", replacement=new,
        )
    if old in _ancestors(store, new):
        raise LifecycleRefused(
            "the replacement depends on the task it replaces; it would never become ready",
            reason_code="replacement_depends_on_old", replacement=new,
        )


def _check_dependents(store: EventStore, old: str) -> list[str]:
    """Refuse while any direct dependent is in flight. (A cycle is impossible once the replacement is
    known not to depend on ``old``: every dependent depends on ``old``, so the replacement cannot be
    among the dependents' ancestors without ``old`` being among its own.)"""
    dependents = _dependents(store, old)
    in_flight = []
    for dependent in dependents:
        state = str(store.status(dependent)["state"])
        if state in (TaskState.LEASED.value, TaskState.RUNNING.value, TaskState.INTEGRATING.value):
            in_flight.append(f"{dependent}:{state}")
    if in_flight:
        raise LifecycleRefused(
            "dependents are in flight (live lease or integrating); let them finish or expire first",
            reason_code="dependent_in_flight", dependents=in_flight,
        )
    return dependents


def _apply_step(
    store: EventStore,
    task: sqlite3.Row,
    step: Step,
    *,
    reason_code: str,
    decision: Mapping[str, Any],
    replacement: str | None,
) -> None:
    task_id = str(task["task_id"])
    latest = _latest_attempt(store, task_id)
    refs = [str(decision["decision_id"])]
    if replacement:
        refs.append(f"replacement:{replacement}")
    event = make_event(
        event_type=step.event,
        reason_code=reason_code,
        actor_role=OPERATOR_ROLE,
        task_id=task_id,
        attempt_id=None if latest is None else str(latest["attempt_id"]),
        work_package_id=str(task["work_package_id"]),
        prior_state=step.from_state,
        next_state=step.to_state,
        contract_hash=_valid_hash(task["contract_hash"]),
        context_hash=_valid_hash(task["context_hash"]),
        artifact_refs=refs,
    )

    def mutation(connection: sqlite3.Connection) -> dict[str, str]:
        current = connection.execute("SELECT state FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if current is None or current["state"] != step.from_state:
            raise _StepRaced
        if connection.execute(
            "SELECT 1 FROM leases WHERE task_id = ? AND active = 1", (task_id,)
        ).fetchone():
            raise LifecycleRefused(
                "the task holds a live lease; expire it first", reason_code="live_lease", task_id=task_id
            )
        transition(current["state"], step.event, TaskState(step.to_state))
        recorded = connection.execute(
            "SELECT payload_json FROM artifacts WHERE artifact_id = ?", (decision["decision_id"],)
        ).fetchone()
        if recorded is None:
            connection.execute(
                "INSERT INTO artifacts(artifact_id, artifact_type, task_id, attempt_id, content_hash, path, "
                "integrable, payload_json) VALUES (?, 'task-cancellation', ?, ?, ?, '', 0, ?)",
                (
                    decision["decision_id"], task_id, None if latest is None else latest["attempt_id"],
                    sha256_of_json(dict(decision)),
                    json.dumps(dict(decision), sort_keys=True, separators=(",", ":")),
                ),
            )
        else:
            stored = json.loads(recorded["payload_json"])
            if (stored["mode"], stored["replacement_task_id"]) != (decision["mode"], decision["replacement_task_id"]):
                raise LifecycleRefused(
                    "a different cancellation decision is already recorded for this task",
                    reason_code="conflicting_decision",
                )
        connection.execute("DELETE FROM semantic_locks WHERE owner_task_id = ?", (task_id,))
        connection.execute(
            "UPDATE tasks SET state = ?, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?",
            (step.to_state, task_id),
        )
        return step.to_payload()

    _execute(store, f"lifecycle:{task_id}:{step.event}:{step.from_state}", event, mutation)


def _resolve_dependent(
    store: EventStore, old: str, dependent: str, *, replacement: str | None, decision_id: str
) -> dict[str, Any]:
    artifact_id = f"dependent-resolution:{old}:{dependent}"
    done = store.rows("SELECT payload_json FROM artifacts WHERE artifact_id = ?", (artifact_id,))
    if done:
        return {**json.loads(done[0]["payload_json"]), "resumed": True}
    row = _task(store, dependent)
    state = str(row["state"])
    if TaskState(state) in TERMINAL_STATES:
        return {"task_id": dependent, "action": "skipped_terminal", "state": state}
    if replacement is not None:
        event_name, target, reason = "dependency_changed", TaskState.STALE, f"superseded_by:{replacement}"
    else:
        event_name, target, reason = "dependency_unavailable", TaskState.BLOCKED, f"dependency_canceled:{old}"
    moves = state != target.value
    latest = _latest_attempt(store, dependent)
    event = make_event(
        event_type=event_name if moves else "dependency_repointed",
        reason_code=reason,
        actor_role=OPERATOR_ROLE,
        task_id=dependent,
        attempt_id=None if latest is None else str(latest["attempt_id"]),
        work_package_id=str(row["work_package_id"]),
        prior_state=state,
        next_state=target.value,
        contract_hash=_valid_hash(row["contract_hash"]),
        context_hash=_valid_hash(row["context_hash"]),
        artifact_refs=[decision_id, f"canceled:{old}"] + ([f"replacement:{replacement}"] if replacement else []),
    )

    def mutation(connection: sqlite3.Connection) -> dict[str, Any]:
        current = connection.execute("SELECT state FROM tasks WHERE task_id = ?", (dependent,)).fetchone()
        if current is None or current["state"] != state:
            raise _StepRaced
        if connection.execute("SELECT 1 FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone():
            raise _StepRaced  # another process resolved it between our check and this transaction
        if connection.execute(
            "SELECT 1 FROM leases WHERE task_id = ? AND active = 1", (dependent,)
        ).fetchone():
            raise LifecycleRefused("a dependent holds a live lease", reason_code="dependent_in_flight")
        repoint = "kept"
        if replacement is not None:
            clash = connection.execute(
                "SELECT 1 FROM dependencies WHERE task_id = ? AND depends_on_task_id = ? AND interface_id = ''",
                (dependent, replacement),
            ).fetchone()
            if clash:
                connection.execute(
                    "DELETE FROM dependencies WHERE task_id = ? AND depends_on_task_id = ? AND interface_id = ''",
                    (dependent, old),
                )
                repoint = "merged_into_existing_dependency"
            else:
                connection.execute(
                    "UPDATE dependencies SET depends_on_task_id = ? WHERE task_id = ? AND depends_on_task_id = ? "
                    "AND interface_id = ''",
                    (replacement, dependent, old),
                )
                repoint = "repointed"
        if moves:
            transition(current["state"], event_name, target)
            connection.execute(
                "UPDATE tasks SET state = ?, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?",
                (target.value, dependent),
            )
        payload = {
            "task_id": dependent, "canceled_task_id": old, "replacement_task_id": replacement,
            "action": event_name if moves else "dependency_repointed", "dependency": repoint,
            "state_before": state, "state_after": target.value if moves else state,
        }
        connection.execute(
            "INSERT INTO artifacts(artifact_id, artifact_type, task_id, attempt_id, content_hash, path, "
            "integrable, payload_json) VALUES (?, 'dependent-resolution', ?, NULL, ?, '', 0, ?)",
            (artifact_id, dependent, sha256_of_json(payload), json.dumps(payload, sort_keys=True, separators=(",", ":"))),
        )
        return payload

    try:
        return dict(_execute(store, f"lifecycle-dependent:{old}:{dependent}:{state}", event, mutation))
    except _StepRaced:
        return _resolve_dependent(store, old, dependent, replacement=replacement, decision_id=decision_id)


def _cancel(
    store: EventStore,
    task_id: str,
    *,
    replacement: str | None,
    reason: str,
    operator: str | None,
    fault_hook: Callable[[str], None] | None,
) -> dict[str, Any]:
    hook = fault_hook or (lambda _point: None)
    reason = _bounded_text(reason, "reason")
    operator = resolve_operator(operator)
    task = _task(store, task_id)
    mode = "supersede" if replacement else "cancel"
    reason_code = f"superseded_by:{replacement}" if replacement else "operator_canceled"
    recorded = _decision(store, task_id)
    resumed = recorded is not None
    if recorded is not None and (recorded["mode"], recorded["replacement_task_id"]) != (mode, replacement):
        raise LifecycleRefused(
            f"task {task_id} already has a recorded {recorded['mode']} decision"
            + (f" (replacement {recorded['replacement_task_id']})" if recorded["replacement_task_id"] else "")
            + "; a different decision cannot replace it",
            reason_code="conflicting_decision",
        )
    state = str(task["state"])
    if not resumed:
        if state == TaskState.CANCELED.value:
            return {"task_id": task_id, "outcome": "already_canceled", "state": state, "lifecycle_record": False,
                    "dependents": []}
        if replacement is not None:
            _check_replacement(store, task_id, replacement, task)
        plan_cancel_chain(state)  # refuse before anything is recorded
        _check_dependents(store, task_id)
    decision = recorded or {
        "artifact_type": "task-cancellation",
        "decision_id": f"task-cancellation:{task_id}",
        "task_id": task_id,
        "work_package_id": str(task["work_package_id"]),
        "mode": mode,
        "replacement_task_id": replacement,
        "reason": reason,
        "operator": operator,
        "operator_identity_authenticated": False,
        "requested_at": _now(),
        "source_state": state,
        "planned_chain": [step.to_payload() for step in plan_cancel_chain(state)],
    }

    hook("planned")
    executed: list[dict[str, str]] = []
    began_without_decision = not resumed
    first = True
    while True:
        task = _task(store, task_id)
        current = str(task["state"])
        if current == TaskState.CANCELED.value:
            break
        if first and began_without_decision and current != state and _decision(store, task_id) is None:
            raise LifecycleRefused(
                f"task {task_id} changed state from {state!r} to {current!r} after the cancellation was "
                "planned (another command won); nothing was changed. Re-run if you still want it canceled",
                reason_code="state_changed",
            )
        chain = plan_cancel_chain(current)
        step = chain[0]
        try:
            _apply_step(store, task, step, reason_code=reason_code, decision=decision, replacement=replacement)
        except _StepRaced:
            if first and began_without_decision and _decision(store, task_id) is None:
                raise LifecycleRefused(
                    f"task {task_id} changed state to {_task(store, task_id)['state']!r} while the "
                    "cancellation was being recorded (another command won); nothing was changed. "
                    "Re-run if you still want it canceled",
                    reason_code="state_changed",
                ) from None
            continue
        first = False
        executed.append(step.to_payload())
        hook(f"after_step:{len(executed)}")

    hook("after_cancel")
    final_decision = _decision(store, task_id) or decision
    dependents = [
        _resolve_dependent(
            store, task_id, dependent,
            replacement=final_decision["replacement_task_id"], decision_id=str(final_decision["decision_id"]),
        )
        for dependent in _dependents(store, task_id)
    ]
    hook("after_dependents")
    return {
        "task_id": task_id,
        "outcome": "canceled" if executed or not resumed else "resumed_complete",
        "state": TaskState.CANCELED.value,
        "mode": final_decision["mode"],
        "replacement_task_id": final_decision["replacement_task_id"],
        "reason_code": reason_code,
        "chain_executed_by_this_call": executed,
        "decision_id": final_decision["decision_id"],
        "dependents": dependents,
        "resumed": resumed,
    }


def supersede_task(
    store: EventStore,
    old_task_id: str,
    *,
    by: str,
    reason: str,
    operator: str | None = None,
    fault_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Cancel ``old_task_id`` because ``by`` replaces it; re-point and stale its dependents."""
    return _cancel(store, old_task_id, replacement=str(by).strip(), reason=reason, operator=operator,
                   fault_hook=fault_hook)


def cancel_task(
    store: EventStore,
    task_id: str,
    *,
    reason: str,
    operator: str | None = None,
    fault_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Cancel ``task_id`` with no replacement; its dependents become ``blocked``."""
    return _cancel(store, task_id, replacement=None, reason=reason, operator=operator, fault_hook=fault_hook)


# --------------------------------------------------------------------------- #
# Read-only view                                                              #
# --------------------------------------------------------------------------- #


def status_chain(store: EventStore, task_id: str) -> dict[str, Any]:
    """The task's lifecycle chain: transitions so far, repair authorizations, the cancel plan, dependents."""
    task = _task(store, task_id)
    transitions = []
    for row in store.rows(
        "SELECT sequence, event_type, reason_code, payload_json FROM events WHERE task_id = ? ORDER BY sequence",
        (task_id,),
    ):
        payload = json.loads(row["payload_json"])
        prior, nxt = payload.get("prior_state"), payload.get("next_state")
        if prior and nxt and prior != nxt:
            transitions.append({"sequence": row["sequence"], "event": row["event_type"],
                                "reason_code": row["reason_code"], "from": prior, "to": nxt})
    state = str(task["state"])
    try:
        plan: Any = [step.to_payload() for step in plan_cancel_chain(state)]
        refusal = None
    except LifecycleRefused as refused:
        plan, refusal = None, {"reason_code": refused.reason_code, "message": refused.message}
    decision = _decision(store, task_id)
    return {
        "task_id": task_id,
        "state": state,
        "attempts": [
            {"attempt_id": row["attempt_id"], "number": row["attempt_number"], "state": row["state"],
             "result_commit": row["result_commit"]}
            for row in store.rows(
                "SELECT attempt_id, attempt_number, state, result_commit FROM attempts WHERE task_id = ? "
                "ORDER BY attempt_number", (task_id,))
        ],
        "attempts_remaining": _attempts_remaining(store, task_id),
        "transitions": transitions,
        "repair_authorizations": [
            {key: a[key] for key in ("authorization_id", "sequence", "previous_attempt_id", "operator", "authorized_at")}
            | {"evidence_ref": a["evidence"]["ref"], "evidence_kind": a["evidence"]["kind"]}
            for a in repair_authorizations(store, task_id)
        ],
        "cancellation": decision,
        "cancel_plan_if_requested": plan,
        "cancel_refusal": refusal,
        "dependents": [{"task_id": d, "state": str(store.status(d)["state"])} for d in _dependents(store, task_id)],
        "orphan_evidence_count": int(task["orphan_evidence_count"]),
    }
