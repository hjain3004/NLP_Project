"""Independent-review pack preparation and identity enforcement."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .errors import ContractStale, IllegalTransition, PermissionViolation
from .events import make_event
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

    def prepare(self, task_id: str) -> dict[str, Any]:
        task_rows = self.store.rows(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        )
        if not task_rows or task_rows[0]["state"] != "candidate":
            raise IllegalTransition("only a candidate can enter independent review", task_id=task_id)
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
        # Deliberately include bindings and objective evidence, not the author's
        # assumptions, decisions, deviations, confidence, or narrative summary.
        pack = {
            "task_id": task_id,
            "attempt_id": attempt["attempt_id"],
            "contract": json.loads(task["payload_json"]),
            "candidate_binding": {
                "base_commit": manifest["base_commit"],
                "result_commit": manifest["result_commit"],
                "contract_hash": manifest["contract_hash"],
                "context_hash": manifest["context_hash"],
                "files_changed": manifest.get("files_changed", []),
            },
            "deterministic_evidence": manifest.get("commands_executed", []),
            "architecture_and_invariants": ["AGENTS.md", "docs/security/INVARIANTS.md"],
        }
        event = make_event(
            event_type="review_prepared",
            reason_code="verification_passed",
            actor_role="scheduler",
            task_id=task_id,
            attempt_id=attempt["attempt_id"],
            prior_state="candidate",
            next_state="independent_review",
            contract_hash=task["contract_hash"],
            context_hash=task["context_hash"],
            repository_commits=[manifest["result_commit"]],
        )

        def mutation(connection: sqlite3.Connection) -> dict[str, Any]:
            changed = connection.execute(
                "UPDATE tasks SET state = 'independent_review', updated_at = CURRENT_TIMESTAMP "
                "WHERE task_id = ? AND state = 'candidate'",
                (task_id,),
            ).rowcount
            if changed != 1:
                raise IllegalTransition("candidate changed before review preparation", task_id=task_id)
            return pack

        return dict(
            self.store.execute(f"review-prepare:{task_id}:{attempt['attempt_id']}", event, mutation)
        )

    def ingest(
        self,
        task_id: str,
        verdict: Mapping[str, Any],
        *,
        reviewer_identity: ReviewIdentity | None = None,
    ) -> dict[str, Any]:
        task_rows = self.store.rows("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        if not task_rows or task_rows[0]["state"] != "independent_review":
            raise IllegalTransition("task is not awaiting an independent verdict", task_id=task_id)
        task = task_rows[0]
        attempt = self._attempt(task_id)
        if verdict.get("task_id") != task_id or verdict.get("attempt_id") != attempt["attempt_id"]:
            raise ContractStale("review verdict names another candidate", task_id=task_id)
        bindings = {
            "reviewed_result_commit": attempt["result_commit"],
            "declared_base_commit": task["base_commit"],
            "contract_hash": task["contract_hash"],
            "context_hash": task["context_hash"],
        }
        for field, expected in bindings.items():
            if verdict.get(field) != expected:
                raise ContractStale("review binding changed", field=field, expected=expected, actual=verdict.get(field))

        author = self.author_identity(task_id)
        attestation = verdict.get("independence_attestation", {})
        if (
            attestation.get("author_session_id") != author.model_session_id
            or attestation.get("author_run_id") != author.provider_run_id
        ):
            raise PermissionViolation("review attestation does not identify the recorded author", task_id=task_id)
        reviewer = reviewer_identity or ReviewIdentity(
            task_id=f"review:{task_id}",
            attempt_id=f"review:{attempt['attempt_id']}",
            provider_run_id=str(verdict.get("reviewer_run_id", "")),
            model_session_id=str(verdict.get("reviewer_session_id", "")),
            actor_role=str(verdict.get("reviewer_role", "")),
        )
        collision = next(
            (dimension for dimension in _IDENTITY_DIMENSIONS if getattr(author, dimension) == getattr(reviewer, dimension)),
            None,
        )
        if collision is not None or attestation.get("distinct") is not True:
            dimension = collision or "independence_attestation"
            event = make_event(
                event_type="review_rejected",
                reason_code="self_review_rejected",
                actor_role="scheduler",
                task_id=task_id,
                attempt_id=attempt["attempt_id"],
                prior_state="independent_review",
                next_state="independent_review",
                artifact_refs=[str(verdict.get("review_id", "unidentified-review"))],
            )
            self.store.execute(
                f"review-rejected:{verdict.get('review_id')}:{dimension}",
                event,
                lambda _connection: {"task_id": task_id, "dimension": dimension},
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
