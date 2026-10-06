"""Level-1 forward-compatible stubs must fail explicitly."""

from __future__ import annotations

import contextlib
import io
import pathlib
import tempfile
import unittest

from agentctl.cli import main
from agentctl.errors import NotImplementedAtLevel1, PlanningError
from agentctl.integrate import integrate_task
from agentctl.routing import route_task
from agentctl.verify import verify_task
from agentctl.store import EventStore


class StubTests(unittest.TestCase):
    def test_python_surfaces_raise_not_implemented_at_level_1(self) -> None:
        for function in (integrate_task, route_task):
            with self.subTest(function=function.__name__), self.assertRaises(
                NotImplementedAtLevel1
            ):
                function("TASK-1")

    def test_verify_and_integrate_commands_exit_nonzero_with_named_error(self) -> None:
        for command in ("integrate",):
            stderr = io.StringIO()
            with self.subTest(command=command), contextlib.redirect_stderr(stderr):
                code = main([command, "TASK-1"])
            self.assertNotEqual(code, 0)
            self.assertIn("not_implemented_at_level_1", stderr.getvalue())

    def test_verify_is_a_real_control_plane_transition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = EventStore(pathlib.Path(temporary) / "runtime.sqlite3")
            with self.assertRaises(PlanningError):
                verify_task("MISSING-TASK", store=store)


if __name__ == "__main__":
    unittest.main()
