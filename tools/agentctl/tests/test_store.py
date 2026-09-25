"""Transactional, idempotent, single-writer event-store tests."""

from __future__ import annotations

import multiprocessing
import pathlib
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from agentctl.events import make_event
from agentctl.store import EventStore


EXPECTED_TABLES = {
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


def _repo_root() -> pathlib.Path:
    output = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()
    return pathlib.Path(output)


def _concurrent_writer(
    database: str,
    root: str,
    name: str,
    entered: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    try:
        store = EventStore(database, repository_root=root)
        event = make_event(
            event_type="work_package_registered",
            reason_code="test",
            actor_role="test",
            work_package_id=name,
        )

        def mutation(connection: sqlite3.Connection) -> dict[str, str]:
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test did not release writer")
            connection.execute(
                "INSERT INTO work_packages(work_package_id, state) VALUES (?, ?)",
                (name, "validated"),
            )
            return {"work_package_id": name}

        results.put(("ok", store.execute(f"register-{name}", event, mutation)))
    except Exception as exc:  # pragma: no cover - reported to the parent process
        results.put(("error", f"{type(exc).__name__}: {exc}"))


class EventStoreTests(unittest.TestCase):
    root = _repo_root()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = pathlib.Path(self.temporary.name) / "runtime.sqlite3"
        self.store = EventStore(self.database, repository_root=self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _event(self, suffix: str = "1") -> dict:
        return make_event(
            event_type="work_package_registered",
            reason_code="test",
            actor_role="test",
            work_package_id=f"WP-{suffix}",
        )

    def test_all_twelve_tables_are_created_and_wal_is_enabled(self) -> None:
        self.assertEqual(self.store.table_names(), EXPECTED_TABLES)
        connection = sqlite3.connect(self.database)
        try:
            mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(mode.lower(), "wal")

    def test_exception_between_state_change_and_event_rolls_back_both(self) -> None:
        def mutation(connection: sqlite3.Connection) -> dict[str, str]:
            connection.execute(
                "INSERT INTO work_packages(work_package_id, state) VALUES (?, ?)",
                ("WP-ROLLBACK", "validated"),
            )
            return {"work_package_id": "WP-ROLLBACK"}

        with mock.patch.object(
            self.store, "_append_event", side_effect=RuntimeError("injected after mutation")
        ):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.store.execute("rollback", self._event("ROLLBACK"), mutation)

        self.assertEqual(self.store.event_count(), 0)
        self.assertEqual(
            self.store.scalar(
                "SELECT COUNT(*) FROM work_packages WHERE work_package_id = ?", ("WP-ROLLBACK",)
            ),
            0,
        )

    def test_read_api_cannot_bypass_the_event_bearing_writer(self) -> None:
        with self.assertRaises(sqlite3.OperationalError):
            self.store.rows(
                "INSERT INTO work_packages(work_package_id, state) VALUES (?, ?)",
                ("WP-BYPASS", "validated"),
            )
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM work_packages"), 0)
        self.assertEqual(self.store.event_count(), 0)

    def test_idempotency_replay_returns_original_result_without_second_mutation(self) -> None:
        calls = 0

        def mutation(connection: sqlite3.Connection) -> dict[str, object]:
            nonlocal calls
            calls += 1
            connection.execute(
                "INSERT INTO work_packages(work_package_id, state) VALUES (?, ?)",
                ("WP-IDEMPOTENT", "validated"),
            )
            return {"work_package_id": "WP-IDEMPOTENT", "sequence": [1, 2, 3]}

        event = self._event("IDEMPOTENT")
        first = self.store.execute("same-key", event, mutation)
        # A retried CLI invocation reconstructs generated event metadata. The
        # operation identity remains the same even though event/trace IDs and
        # the timestamp are fresh.
        second = self.store.execute("same-key", self._event("IDEMPOTENT"), mutation)

        self.assertEqual(second, first)
        self.assertEqual(calls, 1)
        self.assertEqual(self.store.event_count(), 1)

    def test_second_process_writer_is_serialized(self) -> None:
        context = multiprocessing.get_context("spawn")
        first_entered = context.Event()
        first_release = context.Event()
        second_entered = context.Event()
        second_release = context.Event()
        results = context.Queue()

        first = context.Process(
            target=_concurrent_writer,
            args=(
                str(self.database),
                str(self.root),
                "WP-FIRST",
                first_entered,
                first_release,
                results,
            ),
        )
        second = context.Process(
            target=_concurrent_writer,
            args=(
                str(self.database),
                str(self.root),
                "WP-SECOND",
                second_entered,
                second_release,
                results,
            ),
        )
        first.start()
        self.assertTrue(first_entered.wait(5), "first writer never entered its mutation")
        second.start()
        time.sleep(0.2)
        self.assertFalse(second_entered.is_set(), "second writer bypassed the process lock")

        first_release.set()
        self.assertTrue(second_entered.wait(5), "second writer was not released after the first")
        second_release.set()
        first.join(5)
        second.join(5)
        self.assertEqual(first.exitcode, 0)
        self.assertEqual(second.exitcode, 0)
        outcomes = [results.get(timeout=2), results.get(timeout=2)]
        self.assertEqual([outcome[0] for outcome in outcomes], ["ok", "ok"])
        self.assertEqual(self.store.event_count(), 2)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM work_packages"), 2)

    def test_status_snapshot_has_the_normative_json_shape(self) -> None:
        event = make_event(
            event_type="task_registered",
            reason_code="test",
            actor_role="test",
            task_id="TASK-STATUS",
        )

        def mutation(connection: sqlite3.Connection) -> dict[str, str]:
            connection.execute(
                "INSERT INTO tasks(task_id, work_package_id, state) VALUES (?, ?, ?)",
                ("TASK-STATUS", "WP-STATUS", "ready"),
            )
            return {"task_id": "TASK-STATUS"}

        self.store.execute("status-task", event, mutation)
        snapshot = self.store.status("TASK-STATUS")
        self.assertEqual(
            set(snapshot),
            {
                "task_id",
                "state",
                "attempt",
                "lease_epoch",
                "orphan_evidence_count",
                "events",
                "locks",
            },
        )
        self.assertEqual(snapshot["state"], "ready")
        self.assertEqual(snapshot["events"], 1)


if __name__ == "__main__":
    unittest.main()
