"""SQLite runtime state with one transactional writer boundary.

``EventStore.execute`` is the only mutation API. It serializes writers across
threads and processes, starts ``BEGIN IMMEDIATE``, invokes the state mutation,
and appends its event before committing. Any exception rolls back both.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sqlite3
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, TypeVar

from .config import load as load_config
from .config import repo_root as resolve_repo_root
from .events import InvalidEvent, validate_event
from .hashing import canonical_json, sha256_of_json

__all__ = ["EventStore", "TABLE_NAMES"]


TABLE_NAMES: frozenset[str] = frozenset(
    {
        "work_packages",
        "tasks",
        "dependencies",
        "attempts",
        "leases",
        "semantic_locks",
        "artifacts",
        "reviews",
        "integrations",
        "model_runs",
        "budgets",
        "events",
    }
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_packages (
    work_package_id TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'proposed',
    payload_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    work_package_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'draft',
    contract_hash TEXT,
    context_hash TEXT,
    base_commit TEXT,
    capability_class TEXT,
    risk TEXT,
    orphan_evidence_count INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dependencies (
    task_id TEXT NOT NULL,
    depends_on_task_id TEXT NOT NULL DEFAULT '',
    interface_id TEXT NOT NULL DEFAULT '',
    interface_hash TEXT,
    PRIMARY KEY (task_id, depends_on_task_id, interface_id)
);
CREATE TABLE IF NOT EXISTS attempts (
    attempt_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'active',
    worktree_path TEXT,
    author_session_id TEXT,
    author_run_id TEXT,
    result_commit TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS leases (
    lease_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    fencing_token TEXT NOT NULL,
    worker_identity TEXT NOT NULL,
    base_commit TEXT NOT NULL,
    worktree_path TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    budget_json TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS leases_task_epoch ON leases(task_id, epoch DESC);
CREATE TABLE IF NOT EXISTS semantic_locks (
    lock_id TEXT PRIMARY KEY,
    owner_task_id TEXT NOT NULL,
    contract_hash TEXT NOT NULL,
    lease_id TEXT,
    acquired_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    artifact_type TEXT NOT NULL,
    task_id TEXT,
    attempt_id TEXT,
    content_hash TEXT,
    path TEXT,
    integrable INTEGER NOT NULL DEFAULT 1,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS reviews (
    review_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    reviewer_session_id TEXT,
    reviewer_run_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS integrations (
    integration_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    review_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued',
    integration_commit TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS model_runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT,
    attempt_id TEXT,
    provider TEXT,
    model_version TEXT,
    status TEXT NOT NULL,
    usage_json TEXT NOT NULL DEFAULT '{}',
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS budgets (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    attempts_remaining INTEGER,
    repair_cycles_remaining INTEGER,
    artifact_corrections_remaining INTEGER,
    token_remaining INTEGER,
    tool_calls_remaining INTEGER,
    payload_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (scope_type, scope_id)
);
CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    task_id TEXT,
    attempt_id TEXT,
    work_package_id TEXT,
    phase_id TEXT,
    event_type TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    result_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_task_sequence ON events(task_id, sequence);
CREATE INDEX IF NOT EXISTS events_work_package_sequence ON events(work_package_id, sequence);
CREATE TRIGGER IF NOT EXISTS events_append_only_update
BEFORE UPDATE ON events BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS events_append_only_delete
BEFORE DELETE ON events BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;
"""

_RESULT = TypeVar("_RESULT")
_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[Path, threading.RLock] = {}


def _thread_lock(path: Path) -> threading.RLock:
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(path, threading.RLock())


def _idempotency_request_hash(event: Mapping[str, Any]) -> str:
    """Hash stable operation fields, excluding freshly generated metadata."""
    operation = {
        key: value
        for key, value in event.items()
        if key not in {"event_id", "trace_id", "occurred_at"}
    }
    return sha256_of_json(operation)


class EventStore:
    """One local SQLite database and its serialized mutation boundary."""

    def __init__(
        self,
        database: str | os.PathLike[str] | None = None,
        *,
        repository_root: str | os.PathLike[str] | None = None,
    ) -> None:
        if database is None:
            config = load_config()
            config.ensure_runtime_dir()
            self.database = config.runtime_dir / "agentctl.sqlite3"
            self.repository_root = config.repo_root
        else:
            self.database = Path(database).expanduser().resolve()
            self.repository_root = (
                Path(repository_root).expanduser().resolve()
                if repository_root
                else resolve_repo_root()
            )
            self.database.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.database.with_name(self.database.name + ".writer.lock")
        self._thread_lock = _thread_lock(self.lock_path)
        self._initialize()

    def _connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database,
            timeout=30.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        if read_only:
            connection.execute("PRAGMA query_only = ON")
        return connection

    @contextlib.contextmanager
    def _connection(self, *, read_only: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect(read_only=read_only)
        try:
            yield connection
        finally:
            connection.close()

    @contextlib.contextmanager
    def _writer_lock(self) -> Iterator[None]:
        with self._thread_lock:
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                handle = os.fdopen(descriptor, "a+b", closefd=True)
            except Exception:
                os.close(descriptor)
                raise
            with handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _initialize(self) -> None:
        with self._writer_lock(), self._connection() as connection:
            mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(mode).lower() != "wal":
                raise sqlite3.OperationalError(f"could not enable WAL mode: {mode}")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(_SCHEMA)

    def execute(
        self,
        idempotency_key: str,
        event: Mapping[str, Any],
        mutation: Callable[[sqlite3.Connection], _RESULT],
    ) -> _RESULT:
        """Run one state mutation and append its event in one transaction.

        Replaying the same idempotency key and operation returns the
        JSON-normalized original result without invoking ``mutation`` a second
        time. Generated event ID, trace ID, and timestamp metadata may differ;
        reusing a key for different operation fields is rejected.
        """
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise InvalidEvent("idempotency_key must be a nonempty string")
        validate_event(event, repository_root=self.repository_root)
        request_hash = _idempotency_request_hash(event)

        with self._writer_lock(), self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT request_hash, result_json FROM events WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    if existing["request_hash"] != request_hash:
                        raise InvalidEvent(
                            "idempotency key was reused for a different event",
                            idempotency_key=idempotency_key,
                        )
                    result = json.loads(existing["result_json"])
                    connection.commit()
                    return result

                raw_result = mutation(connection)
                result_json = canonical_json(raw_result)
                normalized_result = json.loads(result_json)
                self._append_event(
                    connection,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    event=event,
                    result_json=result_json,
                )
                connection.commit()
                return normalized_result
            except Exception:
                connection.rollback()
                raise

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        idempotency_key: str,
        request_hash: str,
        event: Mapping[str, Any],
        result_json: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO events(
                event_id, idempotency_key, request_hash, trace_id, occurred_at,
                task_id, attempt_id, work_package_id, phase_id, event_type,
                reason_code, payload_json, result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["event_id"],
                idempotency_key,
                request_hash,
                event["trace_id"],
                event["occurred_at"],
                event.get("task_id"),
                event.get("attempt_id"),
                event.get("work_package_id"),
                event.get("phase_id"),
                event["event_type"],
                event["reason_code"],
                canonical_json(event),
                result_json,
            ),
        )

    def rows(self, sql: str, parameters: Sequence[Any] = ()) -> list[sqlite3.Row]:
        """Execute a read-only query on a fresh connection."""
        with self._connection(read_only=True) as connection:
            return list(connection.execute(sql, tuple(parameters)).fetchall())

    def scalar(self, sql: str, parameters: Sequence[Any] = ()) -> Any:
        rows = self.rows(sql, parameters)
        return None if not rows else rows[0][0]

    def table_names(self) -> set[str]:
        rows = self.rows(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
        return {row["name"] for row in rows}

    def event_count(self) -> int:
        return int(self.scalar("SELECT COUNT(*) FROM events"))

    def status(self, task_id: str | None = None) -> dict[str, Any]:
        """Return the normative status JSON shape for one task or all tasks."""
        if task_id is None:
            task_ids = [row["task_id"] for row in self.rows("SELECT task_id FROM tasks ORDER BY task_id")]
            return {"tasks": [self.status(current) for current in task_ids]}

        task = self.rows(
            "SELECT state, orphan_evidence_count FROM tasks WHERE task_id = ?", (task_id,)
        )
        if not task:
            return {
                "task_id": task_id,
                "state": "missing",
                "attempt": None,
                "lease_epoch": 0,
                "orphan_evidence_count": 0,
                "events": 0,
                "locks": [],
            }
        attempt = self.scalar(
            "SELECT MAX(attempt_number) FROM attempts WHERE task_id = ?", (task_id,)
        )
        lease_epoch = self.scalar("SELECT MAX(epoch) FROM leases WHERE task_id = ?", (task_id,))
        events = self.scalar("SELECT COUNT(*) FROM events WHERE task_id = ?", (task_id,))
        locks = [
            row["lock_id"]
            for row in self.rows(
                "SELECT lock_id FROM semantic_locks WHERE owner_task_id = ? ORDER BY lock_id",
                (task_id,),
            )
        ]
        return {
            "task_id": task_id,
            "state": task[0]["state"],
            "attempt": attempt,
            "lease_epoch": int(lease_epoch or 0),
            "orphan_evidence_count": int(task[0]["orphan_evidence_count"]),
            "events": int(events),
            "locks": locks,
        }
