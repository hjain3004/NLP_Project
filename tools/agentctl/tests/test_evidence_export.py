"""Committed dry-run evidence is replayable, filtered, and tamper-evident."""

from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile
import unittest

from agentctl.evidence import export_work_package_events, verify_event_export
from agentctl.events import make_event
from agentctl.store import EventStore


def _repo_root() -> pathlib.Path:
    return pathlib.Path(
        subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
    )


class EvidenceExportTests(unittest.TestCase):
    def test_export_is_filtered_and_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            store = EventStore(directory / "runtime.sqlite3", repository_root=_repo_root())
            for work_package in ("DRY-RUN", "OTHER"):
                event = make_event(
                    event_type="ready_set_computed",
                    reason_code="multiple_ready_tasks",
                    actor_role="scheduler",
                    work_package_id=work_package,
                )
                store.execute(
                    f"ready:{work_package}", event, lambda _connection: {"ok": True}
                )
            destination = directory / "events.json"
            exported = export_work_package_events(store, "DRY-RUN", destination)
            self.assertEqual(exported["event_count"], 1)
            self.assertEqual(len(exported["events"]), 1)
            verify_event_export(destination, repository_root=_repo_root())

            changed = json.loads(destination.read_text())
            changed["events"][0]["reason_code"] = "forged"
            destination.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaises(ValueError):
                verify_event_export(destination, repository_root=_repo_root())


if __name__ == "__main__":
    unittest.main()
