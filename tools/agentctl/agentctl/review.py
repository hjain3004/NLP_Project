"""Independent-review pack preparation and identity enforcement."""

from __future__ import annotations

import json
import datetime as dt
import secrets
import sqlite3
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .errors import ContractStale, IllegalTransition, PermissionViolation
from .events import make_event
from .models import TaskState
from .scheduler import transition
from .store import EventStore

__all__ = ["ReviewIdentity", "ReviewManager"]


@dataclass(frozen=True)
class ReviewIdentity:
    task_id: str
    attempt_id: str
    provider_run_id: str
    model_session_id: str
    actor_role: str


_IDENTITY_DIMENSIONS = (
    "task_id",
    "attempt_id",
    "provider_run_id",
    "model_session_id",
    "actor_role",
)


def _timestamp(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


class ReviewManager:
    def __init__(self, store: EventStore) -> None:
        self.store = store

    def _attempt(self, task_id: str) -> Mapping[str, Any]:
        rows = self.store.rows(
            "SELECT * FROM attempts WHERE task_id = ? ORDER BY attempt_number DESC LIMIT 1",
            (task_id,),
        )
        if not rows:
            raise IllegalTransition("task has no implementation attempt", task_id=task_id)
        return rows[0]

    def set_author_identity(self, task_id: str, identity: ReviewIdentity) -> None:
        attempt = self._attempt(task_id)
        if identity.attempt_id != attempt["attempt_id"]:
            raise ContractStale("author identity names another attempt", task_id=task_id)
        event = make_event(
            event_type="author_identity_recorded",
            reason_code="identity_recorded",
            actor_role="scheduler",
            task_id=task_id,
            attempt_id=identity.attempt_id,
        )

        def mutation(connection: sqlite3.Connection) -> dict[str, str]:
            payload = json.loads(attempt["payload_json"])
            payload.update(
                author_task_id=identity.task_id,
                author_attempt_id=identity.attempt_id,
                author_actor_role=identity.actor_role,
            )
            connection.execute(
                "UPDATE attempts SET author_session_id = ?, author_run_id = ?, payload_json = ? "
                "WHERE attempt_id = ?",
                (
                    identity.model_session_id,
                    identity.provider_run_id,
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    identity.attempt_id,
                ),
            )
            return {"task_id": task_id, "attempt_id": identity.attempt_id}

        self.store.execute(
            f"author-identity:{identity.attempt_id}:{identity.provider_run_id}", event, mutation
        )

    def author_identity(self, task_id: str) -> ReviewIdentity:
        attempt = self._attempt(task_id)
        payload = json.loads(attempt["payload_json"])
        return ReviewIdentity(
            task_id=str(payload.get("author_task_id", task_id)),
            attempt_id=str(payload.get("author_attempt_id", attempt["attempt_id"])),
            provider_run_id=str(attempt["author_run_id"] or attempt["attempt_id"]),
            model_session_id=str(attempt["author_session_id"] or "unknown-author-session"),
            actor_role=str(payload.get("author_actor_role", "implementation")),
        )

    def _record_rejection(
        self,
        task_id: str,
        attempt_id: str,
        *,
        reason_code: str,
        dimension: str,
        artifact_ref: str,
    ) -> None:
        event = make_event(
            event_type="review_rejected",
            reason_code=reason_code,
            actor_role="scheduler",
            task_id=task_id,
            attempt_id=attempt_id,
            prior_state="independent_review",
            next_state="independent_review",
            artifact_refs=[artifact_ref, f"collision:{dimension}"],
        )
        self.store.execute(
            f"review-rejected:{task_id}:{artifact_ref}:{dimension}:{uuid.uuid4()}",
            event,
            lambda _connection: {"task_id": task_id, "dimension": dimension},
        )

    def assign(self, task_id: str, reviewer: ReviewIdentity) -> dict[str, Any]:
        """Register trusted reviewer dispatch identity and issue its fenced lease."""
        task_rows = self.store.rows("SELECT state FROM tasks WHERE task_id = ?", (task_id,))
        if not task_rows or task_rows[0]["state"] != "independent_review":
            raise IllegalTransition("task is not awaiting reviewer assignment", task_id=task_id)
        attempt = self._attempt(task_id)
        author = self.author_identity(task_id)
        collision = next(
            (
                dimension
                for dimension in _IDENTITY_DIMENSIONS
                if getattr(author, dimension) == getattr(reviewer, dimension)
            ),
            None,
        )
        if collision is not None:
            self._record_rejection(
                task_id,
                str(attempt["attempt_id"]),
                reason_code="self_review_rejected",
                dimension=collision,
                artifact_ref=f"reviewer-assignment:{reviewer.model_session_id}",
            )
            raise PermissionViolation(
                "reviewer identity is not independent",
                task_id=task_id,
                dimension=collision,
            )

        previous_epoch = self.store.scalar(
            "SELECT MAX(CAST(json_extract(payload_json, '$.lease_epoch') AS INTEGER)) "
            "FROM artifacts WHERE task_id = ? AND artifact_type = 'review-assignment'",
            (task_id,),
        )
        assignment = {
            "reviewer_task_id": reviewer.task_id,
            "reviewer_attempt_id": reviewer.attempt_id,
            "reviewer_run_id": reviewer.provider_run_id,
            "reviewer_session_id": reviewer.model_session_id,
            "reviewer_role": reviewer.actor_role,
            "lease_id": str(uuid.uuid4()),
            "lease_epoch": int(previous_epoch or 0) + 1,
            "fencing_token": secrets.token_hex(32),
            "expires_at": _timestamp(
                dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=30)
            ),
        }
        candidate = self._trusted_candidate_binding(task_id, str(attempt["attempt_id"]))
        assignment.update(
            reviewed_result_commit=candidate["result_commit"],
            tree_hash=candidate["tree_hash"],
            diff_hash=candidate["diff_hash"],
            declared_base_commit=candidate["base_commit"],
            contract_hash=candidate["contract_hash"],
            context_hash=candidate["context_hash"],
            verification_evidence_hash=candidate["verification_evidence_hash"],
        )
        artifact_id = f"review-assignment:{task_id}:{assignment['lease_id']}"
        event = make_event(
            event_type="review_assigned",
            reason_code="independent_reviewer_reserved",
            actor_role="scheduler",
            task_id=task_id,
            attempt_id=str(attempt["attempt_id"]),
            prior_state="independent_review",
            next_state="independent_review",
            artifact_refs=[artifact_id],
        )

        def mutation(connection: sqlite3.Connection) -> dict[str, Any]:
            connection.execute(
                "UPDATE artifacts SET integrable = 0 WHERE task_id = ? "
                "AND artifact_type = 'review-assignment' AND integrable = 1",
                (task_id,),
            )
            connection.execute(
                "INSERT INTO artifacts(artifact_id, artifact_type, task_id, attempt_id, "
                "content_hash, path, integrable, payload_json) VALUES "
                "(?, 'review-assignment', ?, ?, ?, '', 1, ?)",
                (
                    artifact_id,
                    task_id,
                    attempt["attempt_id"],
                    assignment["fencing_token"],
                    json.dumps(assignment, sort_keys=True, separators=(",", ":")),
                ),
            )
            return assignment

        return dict(
            self.store.execute(
                f"review-assign:{task_id}:{assignment['lease_id']}", event, mutation
            )
        )

    def prepare(self, task_id: str) -> dict[str, Any]:
        task_rows = self.store.rows(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        )
        if not task_rows or task_rows[0]["state"] != "independent_review":
            raise IllegalTransition(
                "only a deterministically verified candidate can enter independent review",
                task_id=task_id,
            )
        task = task_rows[0]
        attempt = self._attempt(task_id)
        artifact_rows = self.store.rows(
            "SELECT payload_json FROM artifacts WHERE task_id = ? AND attempt_id = ? "
            "AND artifact_type = 'implementation-manifest' AND integrable = 1 LIMIT 1",
            (task_id, attempt["attempt_id"]),
        )
        if not artifact_rows:
            raise IllegalTransition("candidate has no integrable implementation manifest", task_id=task_id)
        manifest = json.loads(artifact_rows[0]["payload_json"])
        candidate = self._trusted_candidate_binding(task_id, str(attempt["attempt_id"]))
        # Deliberately include bindings and objective evidence, not the author's
        # assumptions, decisions, deviations, confidence, or narrative summary.
        pack = {
            "task_id": task_id,
            "attempt_id": attempt["attempt_id"],
            "contract": json.loads(task["payload_json"]),
            "candidate_binding": {
                **candidate,
                "files_changed": manifest.get("files_changed", []),
            },
            "deterministic_evidence": candidate["verification_evidence"],
            "architecture_and_invariants": ["AGENTS.md", "docs/security/INVARIANTS.md"],
        }
        event = make_event(
            event_type="review_pack_prepared",
            reason_code="trusted_review_pack_prepared",
            actor_role="scheduler",
            task_id=task_id,
            attempt_id=attempt["attempt_id"],
            prior_state="independent_review",
            next_state="independent_review",
            contract_hash=task["contract_hash"],
            context_hash=task["context_hash"],
            repository_commits=[manifest["result_commit"]],
        )

        def mutation(connection: sqlite3.Connection) -> dict[str, Any]:
            current = connection.execute(
                "SELECT state FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if current is None or current["state"] != "independent_review":
                raise IllegalTransition("candidate changed before review preparation", task_id=task_id)
            return pack

        return dict(
            self.store.execute(f"review-prepare:{task_id}:{attempt['attempt_id']}", event, mutation)
        )

    def ingest(
        self,
        task_id: str,
        verdict: Mapping[str, Any],
    ) -> dict[str, Any]:
        task_rows = self.store.rows("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        if not task_rows or task_rows[0]["state"] != "independent_review":
            raise IllegalTransition("task is not awaiting an independent verdict", task_id=task_id)
        task = task_rows[0]
        attempt = self._attempt(task_id)
        if verdict.get("task_id") != task_id or verdict.get("attempt_id") != attempt["attempt_id"]:
            raise ContractStale("review verdict names another candidate", task_id=task_id)
        author = self.author_identity(task_id)
        attestation = verdict.get("independence_attestation", {})
        if (
            attestation.get("author_session_id") != author.model_session_id
            or attestation.get("author_run_id") != author.provider_run_id
        ):
            raise PermissionViolation("review attestation does not identify the recorded author", task_id=task_id)
        assignment_rows = self.store.rows(
            "SELECT artifact_id, payload_json FROM artifacts WHERE task_id = ? "
            "AND artifact_type = 'review-assignment' AND integrable = 1 "
            "ORDER BY rowid DESC LIMIT 1",
            (task_id,),
        )
        if not assignment_rows:
            raise PermissionViolation(
                "review verdict has no trusted reviewer assignment", task_id=task_id
            )
        assignment_id = str(assignment_rows[0]["artifact_id"])
        assignment = json.loads(assignment_rows[0]["payload_json"])
        bindings = {
            "reviewed_result_commit": assignment["reviewed_result_commit"],
            "tree_hash": assignment["tree_hash"],
            "diff_hash": assignment["diff_hash"],
            "declared_base_commit": assignment["declared_base_commit"],
            "contract_hash": assignment["contract_hash"],
            "context_hash": assignment["context_hash"],
            "verification_evidence_hash": assignment["verification_evidence_hash"],
        }
        for field, expected in bindings.items():
            if verdict.get(field) != expected:
                raise ContractStale(
                    "review binding changed",
                    field=field,
                    expected=expected,
                    actual=verdict.get(field),
                )
        try:
            expires_at = dt.datetime.fromisoformat(
                str(assignment["expires_at"]).replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PermissionViolation(
                "reviewer assignment has no valid expiry",
                task_id=task_id,
                dimension="lease_expired",
            ) from exc
        if expires_at <= dt.datetime.now(dt.timezone.utc):
            self._record_rejection(
                task_id,
                str(attempt["attempt_id"]),
                reason_code="review_lease_expired",
                dimension="lease_expired",
                artifact_ref=str(verdict.get("review_id", "unidentified-review")),
            )
            raise PermissionViolation(
                "reviewer assignment expired", task_id=task_id, dimension="lease_expired"
            )
        expected_fields = {
            "reviewer_run_id": assignment["reviewer_run_id"],
            "reviewer_session_id": assignment["reviewer_session_id"],
            "reviewer_role": assignment["reviewer_role"],
            "lease_id": assignment["lease_id"],
            "lease_epoch": assignment["lease_epoch"],
            "fencing_token": assignment["fencing_token"],
        }
        mismatch = next(
            (field for field, expected in expected_fields.items() if verdict.get(field) != expected),
            None,
        )
        if mismatch is not None:
            self._record_rejection(
                task_id,
                str(attempt["attempt_id"]),
                reason_code="review_identity_mismatch",
                dimension=mismatch,
                artifact_ref=str(verdict.get("review_id", "unidentified-review")),
            )
            raise PermissionViolation(
                "review verdict does not match trusted reviewer assignment",
                task_id=task_id,
                dimension=mismatch,
            )
        reviewer = ReviewIdentity(
            task_id=str(assignment["reviewer_task_id"]),
            attempt_id=str(assignment["reviewer_attempt_id"]),
            provider_run_id=str(assignment["reviewer_run_id"]),
            model_session_id=str(assignment["reviewer_session_id"]),
            actor_role=str(assignment["reviewer_role"]),
        )
        collision = next(
            (dimension for dimension in _IDENTITY_DIMENSIONS if getattr(author, dimension) == getattr(reviewer, dimension)),
            None,
        )
        if collision is not None or attestation.get("distinct") is not True:
            dimension = collision or "independence_attestation"
            self._record_rejection(
                task_id,
                str(attempt["attempt_id"]),
                reason_code="self_review_rejected",
                dimension=dimension,
                artifact_ref=str(verdict.get("review_id", "unidentified-review")),
            )
            raise PermissionViolation(
                "reviewer identity is not independent",
                task_id=task_id,
                dimension=dimension,
            )

        review_id = str(verdict["review_id"])
        blocking = verdict.get("verdict") != "no_blocking_findings" or any(
            finding.get("blocking") is True for finding in verdict.get("findings", ())
        )
        next_state = "repair_required" if blocking else "integration_ready"
        event = make_event(
            event_type="review_blocked" if blocking else "review_passed",
            reason_code="blocking_findings" if blocking else "no_blocking_findings",
            actor_role="scheduler",
            task_id=task_id,
            attempt_id=attempt["attempt_id"],
            prior_state="independent_review",
            next_state=next_state,
            contract_hash=task["contract_hash"],
            context_hash=task["context_hash"],
            artifact_refs=[review_id],
        )

        def mutation(connection: sqlite3.Connection) -> dict[str, Any]:
            current = connection.execute(
                "SELECT state FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if current is None:
                raise IllegalTransition("task disappeared before review ingestion", task_id=task_id)
            transition(
                current["state"],
                "review_blocked" if blocking else "review_passed",
                TaskState(next_state),
            )
            consumed = connection.execute(
                "UPDATE artifacts SET integrable = 0 WHERE artifact_id = ? AND integrable = 1",
                (assignment_id,),
            ).rowcount
            if consumed != 1:
                raise PermissionViolation(
                    "reviewer assignment was already consumed", task_id=task_id
                )
            connection.execute(
                "INSERT INTO reviews(review_id, task_id, attempt_id, verdict, "
                "reviewer_session_id, reviewer_run_id, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    review_id,
                    task_id,
                    attempt["attempt_id"],
                    verdict["verdict"],
                    reviewer.model_session_id,
                    reviewer.provider_run_id,
                    json.dumps(dict(verdict), sort_keys=True, separators=(",", ":")),
                ),
            )
            connection.execute(
                "UPDATE tasks SET state = ?, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?",
                (next_state, task_id),
            )
            return {"review_id": review_id, "task_id": task_id, "state": next_state}

        return dict(self.store.execute(f"review-ingest:{review_id}", event, mutation))

    def _trusted_candidate_binding(self, task_id: str, attempt_id: str) -> dict[str, Any]:
        candidate_rows = self.store.rows(
            "SELECT payload_json FROM artifacts WHERE artifact_id = ? AND integrable = 1",
            (f"candidate-binding:{task_id}:{attempt_id}",),
        )
        verification_rows = self.store.rows(
            "SELECT content_hash, payload_json FROM artifacts WHERE artifact_id = ? "
            "AND integrable = 1",
            (f"verification:{task_id}:{attempt_id}",),
        )
        if not candidate_rows or not verification_rows:
            raise IllegalTransition(
                "independent review lacks trusted candidate verification",
                task_id=task_id,
                attempt_id=attempt_id,
            )
        candidate = json.loads(candidate_rows[0]["payload_json"])
        candidate["verification_evidence_hash"] = str(
            verification_rows[0]["content_hash"]
        )
        candidate["verification_evidence"] = json.loads(
            verification_rows[0]["payload_json"]
        )
        return candidate
