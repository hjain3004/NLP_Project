"""Level-1 forward-compatible stubs must fail explicitly."""

from __future__ import annotations

import contextlib
import io
import unittest

from agentctl.cli import main
from agentctl.errors import NotImplementedAtLevel1
from agentctl.integrate import integrate_task
from agentctl.routing import route_task
from agentctl.verify import verify_task


class StubTests(unittest.TestCase):
    def test_python_surfaces_raise_not_implemented_at_level_1(self) -> None:
        for function in (verify_task, integrate_task, route_task):
            with self.subTest(function=function.__name__), self.assertRaises(
                NotImplementedAtLevel1
            ):
                function("TASK-1")

    def test_verify_and_integrate_commands_exit_nonzero_with_named_error(self) -> None:
        for command in ("verify", "integrate"):
            stderr = io.StringIO()
            with self.subTest(command=command), contextlib.redirect_stderr(stderr):
                code = main([command, "TASK-1"])
            self.assertNotEqual(code, 0)
            self.assertIn("not_implemented_at_level_1", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
