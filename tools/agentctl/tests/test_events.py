"""Event schema and telemetry privacy tests."""

from __future__ import annotations

import copy
import json
import pathlib
import subprocess
import tempfile
import unittest

from agentctl.events import InvalidEvent, make_event, validate_event
from agentctl.store import EventStore


def _repo_root() -> pathlib.Path:
    output = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()
    return pathlib.Path(output)


class EventValidationTests(unittest.TestCase):
    root = _repo_root()

    def test_valid_minimal_event_is_accepted(self) -> None:
        event = make_event(
            event_type="task_registered",
            reason_code="contract_validated",
            actor_role="control-plane",
            task_id="TASK-1",
            repository_commits=["a" * 40],
        )
        self.assertIs(validate_event(event, repository_root=self.root), event)

    def test_denylisted_key_is_rejected_at_any_depth(self) -> None:
        event = make_event(event_type="test", reason_code="test", actor_role="test")
        event["artifact_refs"] = [{"safe": {"document_text": "synthetic secret"}}]
        with self.assertRaises(InvalidEvent) as caught:
            validate_event(event, repository_root=self.root)
        self.assertEqual(caught.exception.code, "invalid_event")
        self.assertIn("document_text", str(caught.exception))

    def test_string_limit_counts_utf8_bytes(self) -> None:
        event = make_event(event_type="test", reason_code="test", actor_role="test")
        event["reason_code"] = "é" * 1025
        with self.assertRaises(InvalidEvent) as caught:
            validate_event(event, repository_root=self.root)
        self.assertIn("2048", str(caught.exception))

    def test_schema_failure_is_invalid_event(self) -> None:
        event = make_event(event_type="test", reason_code="test", actor_role="test")
        del event["trace_id"]
        with self.assertRaises(InvalidEvent):
            validate_event(event, repository_root=self.root)

    def test_stateful_document_text_fixture_is_rejected(self) -> None:
        path = (
            self.root
            / "agent-system"
            / "examples"
            / "invalid"
            / "stateful"
            / "09-event-contains-document-text.json"
        )
        event = json.loads(path.read_text(encoding="utf-8"))
        with self.assertRaises(InvalidEvent) as caught:
            validate_event(event, repository_root=self.root)
        self.assertIn("text", str(caught.exception))

    def test_invalid_event_writes_zero_rows(self) -> None:
        event = make_event(event_type="test", reason_code="test", actor_role="test")
        invalid = copy.deepcopy(event)
        invalid["prompt"] = "must not persist"
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(
                pathlib.Path(directory) / "runtime.sqlite3", repository_root=self.root
            )
            with self.assertRaises(InvalidEvent):
                store.execute("invalid-event", invalid, lambda connection: {"ok": True})
            self.assertEqual(store.event_count(), 0)


if __name__ == "__main__":
    unittest.main()
