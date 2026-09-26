"""Persistent leases with monotonic epochs and opaque fencing tokens."""

from __future__ import annotations

import datetime as dt
import json
import secrets
import sqlite3
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .errors import BudgetExhausted, IllegalTransition
from .events import make_event
from .locks import write_sets_conflict
from .store import EventStore

__all__ = ["Lease", "LeaseManager"]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _timestamp(value: dt.datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Lease:
    lease_id: str
    task_id: str
    attempt_id: str
    epoch: int
    fencing_token: str
    worker_identity: str
    base_commit: str
    worktree_path: str
    expires_at: str
    heartbeat_at: str
    locks: Mapping[str, Any]
    budget: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Lease":
        return cls(
            lease_id=str(value["lease_id"]),
            task_id=str(value["task_id"]),
            attempt_id=str(value["attempt_id"]),
            epoch=int(value["epoch"]),
            fencing_token=str(value["fencing_token"]),
            worker_identity=str(value["worker_identity"]),
            base_commit=str(value["base_commit"]),
            worktree_path=str(value["worktree_path"]),
            expires_at=str(value["expires_at"]),
            heartbeat_at=str(value["heartbeat_at"]),
            locks=dict(value.get("locks", {})),
            budget=dict(value.get("budget", {})),
        )


class LeaseManager:
    """Own lease issuance, acknowledgement, heartbeats, and expiry."""

    def __init__(self, store: EventStore, *, duration_seconds: int = 1800) -> None:
        self.store = store
        self.duration_seconds = duration_seconds

    def issue(self, spec: Any, *, worker_identity: str) -> Lease:
        issued_at = _now()
        heartbeat_at = _timestamp(issued_at)
        expires_at = _timestamp(issued_at + dt.timedelta(seconds=self.duration_seconds))
        lease_id = str(uuid.uuid4())
        fencing_token = secrets.token_urlsafe(32)

        event = make_event(
            event_type="lease_issued",
            reason_code="admission_passed",
            actor_role="scheduler",
            task_id=spec.task_id,
            work_package_id=spec.work_package_id,
            prior_state="ready",
            next_state="leased",
            contract_hash=spec.contract_hash,
            context_hash=spec.context_hash,
            repository_commits=[spec.base_commit],
        )

        def mutation(connection: sqlite3.Connection) -> dict[str, Any]:
            task = connection.execute(
                "SELECT state FROM tasks WHERE task_id = ?", (spec.task_id,)
            ).fetchone()
            if task is None or task["state"] != "ready":
                raise IllegalTransition(
                    "only a ready task can receive a lease",
                    task_id=spec.task_id,
                    state=None if task is None else task["state"],
                )
            if connection.execute(
                "SELECT 1 FROM leases WHERE task_id = ? AND active = 1",
                (spec.task_id,),
            ).fetchone():
                raise IllegalTransition("task already has an active lease", task_id=spec.task_id)

            # Admission is computed before entering the writer boundary, so
            # conflict checks are repeated here to close the concurrent-
            # dispatcher race.  The transaction is the authoritative check.
            for active in connection.execute(
                "SELECT t.task_id, t.payload_json FROM tasks t JOIN leases l USING(task_id) "
                "WHERE l.active = 1 AND t.task_id != ?",
                (spec.task_id,),
            ).fetchall():
                payload = json.loads(active["payload_json"])
                if write_sets_conflict(
                    spec.writable_paths, payload.get("writable_paths", ())
                ):
                    raise IllegalTransition(
                        "write set conflicts with an active lease",
                        task_id=spec.task_id,
                        owner_task_id=active["task_id"],
                    )
            for lock_id in spec.semantic_locks:
                owner = connection.execute(
                    "SELECT owner_task_id FROM semantic_locks "
                    "WHERE lock_id = ? AND owner_task_id != ?",
                    (lock_id, spec.task_id),
                ).fetchone()
                if owner is not None:
                    raise IllegalTransition(
                        "semantic lock conflicts with an active lease",
                        task_id=spec.task_id,
                        lock_id=lock_id,
                        owner_task_id=owner["owner_task_id"],
                    )

            budget_row = connection.execute(
                "SELECT attempts_remaining, artifact_corrections_remaining "
                "FROM budgets WHERE scope_type = 'task' AND scope_id = ?",
                (spec.task_id,),
            ).fetchone()
            if budget_row is None or int(budget_row["attempts_remaining"] or 0) <= 0:
                raise BudgetExhausted("task attempt budget is exhausted", task_id=spec.task_id)

            epoch = int(
                connection.execute(
                    "SELECT COALESCE(MAX(epoch), 0) + 1 FROM leases WHERE task_id = ?",
                    (spec.task_id,),
                ).fetchone()[0]
            )
            attempt_id = f"{spec.task_id}-attempt-{epoch}"
            worktree_path = f"agent-system/runtime/worktrees/{spec.task_id}/attempt-{epoch}"
            budget = {
                "attempts_remaining": int(budget_row["attempts_remaining"]) - 1,
                "artifact_corrections_remaining": int(
                    budget_row["artifact_corrections_remaining"] or 0
                ),
            }
            connection.execute(
                "UPDATE budgets SET attempts_remaining = ? "
                "WHERE scope_type = 'task' AND scope_id = ?",
                (budget["attempts_remaining"], spec.task_id),
            )
            connection.execute(
                "INSERT INTO attempts(attempt_id, task_id, attempt_number, state, "
                "worktree_path, author_session_id) VALUES (?, ?, ?, 'leased', ?, ?)",
                (attempt_id, spec.task_id, epoch, worktree_path, worker_identity),
            )
            connection.execute(
                "INSERT INTO leases(lease_id, task_id, attempt_id, epoch, fencing_token, "
                "worker_identity, base_commit, worktree_path, expires_at, heartbeat_at, "
                "budget_json, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (
                    lease_id,
                    spec.task_id,
                    attempt_id,
                    epoch,
                    fencing_token,
                    worker_identity,
                    spec.base_commit,
                    worktree_path,
                    expires_at,
                    heartbeat_at,
                    json.dumps(budget, sort_keys=True, separators=(",", ":")),
                ),
            )
            for lock_id, contract_hash in sorted(spec.semantic_locks.items()):
                connection.execute(
                    "INSERT INTO semantic_locks(lock_id, owner_task_id, contract_hash, lease_id) "
                    "VALUES (?, ?, ?, ?)",
                    (lock_id, spec.task_id, contract_hash, lease_id),
                )
            connection.execute(
                "UPDATE tasks SET state = 'leased', updated_at = CURRENT_TIMESTAMP "
                "WHERE task_id = ?",
                (spec.task_id,),
            )
            return {
                "lease_id": lease_id,
                "task_id": spec.task_id,
                "attempt_id": attempt_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
                "worker_identity": worker_identity,
                "base_commit": spec.base_commit,
                "worktree_path": worktree_path,
                "expires_at": expires_at,
                "heartbeat_at": heartbeat_at,
                "locks": {
                    "writable_paths": list(spec.writable_paths),
                    "semantic_locks": dict(spec.semantic_locks),
                },
                "budget": budget,
            }

        result = self.store.execute(f"lease-issue:{lease_id}", event, mutation)
        return Lease.from_mapping(result)

    def acknowledge(self, task_id: str, context_hash: str) -> Lease:
        expired = self.expire_dead()
        if task_id in expired:
            raise IllegalTransition("lease expired before acknowledgement", task_id=task_id)
        rows = self.store.rows(
            "SELECT l.*, t.context_hash, t.payload_json FROM leases l JOIN tasks t USING(task_id) "
            "WHERE l.task_id = ? AND l.active = 1 ORDER BY l.epoch DESC LIMIT 1",
            (task_id,),
        )
        if not rows:
            raise IllegalTransition("task has no active lease", task_id=task_id)
        row = rows[0]
        if row["context_hash"] != context_hash:
            raise IllegalTransition("worker acknowledged a different context", task_id=task_id)
        event = make_event(
            event_type="worker_acknowledged",
            reason_code="context_acknowledged",
            actor_role="scheduler",
            task_id=task_id,
            attempt_id=row["attempt_id"],
            prior_state="leased",
            next_state="running",
            context_hash=context_hash,
        )

        def mutation(connection: sqlite3.Connection) -> dict[str, Any]:
            changed = connection.execute(
                "UPDATE tasks SET state = 'running', updated_at = CURRENT_TIMESTAMP "
                "WHERE task_id = ? AND state = 'leased'",
                (task_id,),
            ).rowcount
            if changed != 1:
                raise IllegalTransition("task is not awaiting acknowledgement", task_id=task_id)
            connection.execute(
                "UPDATE attempts SET state = 'running' WHERE attempt_id = ?",
                (row["attempt_id"],),
            )
            return self._row_result(row)

        result = self.store.execute(
            f"lease-ack:{row['lease_id']}:{context_hash}", event, mutation
        )
        return Lease.from_mapping(result)

    def expire(self, task_id: str, *, force: bool = False) -> bool:
        rows = self.store.rows(
            "SELECT * FROM leases WHERE task_id = ? AND active = 1 "
            "ORDER BY epoch DESC LIMIT 1",
            (task_id,),
        )
        if not rows:
            return False
        row = rows[0]
        if not force and str(row["expires_at"]) > _timestamp(_now()):
            return False
        event = make_event(
            event_type="lease_expired",
            reason_code="lease_expired",
            actor_role="scheduler",
            task_id=task_id,
            attempt_id=row["attempt_id"],
            prior_state=self.store.status(task_id)["state"],
            next_state="ready",
        )

        def mutation(connection: sqlite3.Connection) -> bool:
            changed = connection.execute(
                "UPDATE leases SET active = 0 WHERE lease_id = ? AND active = 1",
                (row["lease_id"],),
            ).rowcount
            if changed != 1:
                return False
            connection.execute(
                "UPDATE attempts SET state = 'expired', ended_at = CURRENT_TIMESTAMP "
                "WHERE attempt_id = ?",
                (row["attempt_id"],),
            )
            connection.execute("DELETE FROM semantic_locks WHERE lease_id = ?", (row["lease_id"],))
            connection.execute(
                "UPDATE tasks SET state = 'ready', updated_at = CURRENT_TIMESTAMP "
                "WHERE task_id = ?",
                (task_id,),
            )
            return True

        return bool(self.store.execute(f"lease-expire:{row['lease_id']}", event, mutation))

    def heartbeat(self, task_id: str, lease_id: str, fencing_token: str) -> str:
        expired = self.expire_dead()
        if task_id in expired:
            raise IllegalTransition("lease expired before its heartbeat", task_id=task_id)
        rows = self.store.rows(
            "SELECT * FROM leases WHERE task_id = ? AND lease_id = ? AND active = 1",
            (task_id, lease_id),
        )
        if not rows or rows[0]["fencing_token"] != fencing_token:
            raise IllegalTransition("heartbeat does not name the active fenced lease", task_id=task_id)
        row = rows[0]
        heartbeat_at = _timestamp(_now())
        event = make_event(
            event_type="lease_heartbeat",
            reason_code="lease_alive",
            actor_role="scheduler",
            task_id=task_id,
            attempt_id=row["attempt_id"],
        )

        def mutation(connection: sqlite3.Connection) -> str:
            changed = connection.execute(
                "UPDATE leases SET heartbeat_at = ? WHERE lease_id = ? AND active = 1",
                (heartbeat_at, lease_id),
            ).rowcount
            if changed != 1:
                raise IllegalTransition("lease expired before its heartbeat", task_id=task_id)
            return heartbeat_at

        return str(
            self.store.execute(
                f"lease-heartbeat:{lease_id}:{heartbeat_at}", event, mutation
            )
        )

    def expire_dead(self) -> tuple[str, ...]:
        now = _timestamp(_now())
        task_ids = [
            row["task_id"]
            for row in self.store.rows(
                "SELECT task_id FROM leases WHERE active = 1 AND expires_at <= ? "
                "ORDER BY task_id",
                (now,),
            )
        ]
        return tuple(task_id for task_id in task_ids if self.expire(task_id))

    @staticmethod
    def _row_result(row: Mapping[str, Any]) -> dict[str, Any]:
        task_payload = json.loads(row["payload_json"]) if "payload_json" in row.keys() else {}
        return {
            "lease_id": row["lease_id"],
            "task_id": row["task_id"],
            "attempt_id": row["attempt_id"],
            "epoch": row["epoch"],
            "fencing_token": row["fencing_token"],
            "worker_identity": row["worker_identity"],
            "base_commit": row["base_commit"],
            "worktree_path": row["worktree_path"],
            "expires_at": row["expires_at"],
            "heartbeat_at": row["heartbeat_at"],
            "locks": {
                "writable_paths": list(task_payload.get("writable_paths", ())),
                "semantic_locks": dict(task_payload.get("semantic_locks", {})),
            },
            "budget": json.loads(row["budget_json"]),
        }
