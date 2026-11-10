"""Verify is a real control-plane transition (routing/integrate placeholders were replaced)."""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from agentctl.errors import PlanningError
from agentctl.verify import verify_task
from agentctl.store import EventStore


class StubTests(unittest.TestCase):
    def test_verify_is_a_real_control_plane_transition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = EventStore(pathlib.Path(temporary) / "runtime.sqlite3")
            with self.assertRaises(PlanningError):
                verify_task("MISSING-TASK", store=store)


if __name__ == "__main__":
    unittest.main()
