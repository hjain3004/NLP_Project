"""ContextCompiler task-contract discovery must ignore agent-system/runtime/."""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
from unittest import mock

from agentctl.context import ContextCompiler
from agentctl.errors import PlanningError

_BOUNDARY_POLICY = {
    "enforcement": {
        "allowed_context_roots": ["agent-system/**", "AGENTS.md", "docs/**"],
        "excluded_by_default": [],
    }
}


def _write_json(path: pathlib.Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def _task_contract(task_id: str) -> dict:
    return {
        "artifact_type": "task-contract",
        "identity": {"task_id": task_id},
    }


def _make_repo(root: pathlib.Path) -> None:
    _write_json(root / "agent-system/policies/product-data-boundary.json", _BOUNDARY_POLICY)


class RuntimeExclusionTests(unittest.TestCase):
    def test_discovery_ignores_runtime_even_when_it_has_a_matching_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            _make_repo(root)
            real_path = root / "agent-system/work/DEMO/tasks/DEMO-900.json"
            _write_json(real_path, _task_contract("DEMO-900"))
            runtime_path = (
                root
                / "agent-system/runtime/worktrees/attempt-1/agent-system/work/DEMO/tasks/DEMO-900.json"
            )
            _write_json(runtime_path, _task_contract("DEMO-900"))

            compiler = ContextCompiler(root)
            found_path, document = compiler._find_task_contract("DEMO-900")

            self.assertEqual(found_path, "agent-system/work/DEMO/tasks/DEMO-900.json")
            self.assertEqual(document["identity"]["task_id"], "DEMO-900")

    def test_real_contract_wins_regardless_of_path_sort_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            _make_repo(root)
            real_path = root / "agent-system/work/zz-demo/tasks/DEMO-901.json"
            runtime_path = root / "agent-system/runtime/aa-worktree/DEMO-901.json"
            _write_json(real_path, _task_contract("DEMO-901"))
            _write_json(runtime_path, _task_contract("DEMO-901"))
            self.assertLess(
                runtime_path.relative_to(root).as_posix(),
                real_path.relative_to(root).as_posix(),
            )

            compiler = ContextCompiler(root)
            found_path, _document = compiler._find_task_contract("DEMO-901")

            self.assertEqual(found_path, "agent-system/work/zz-demo/tasks/DEMO-901.json")

    def test_discovery_identical_when_no_runtime_directory_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            _make_repo(root)
            real_path = root / "agent-system/work/DEMO/tasks/DEMO-902.json"
            _write_json(real_path, _task_contract("DEMO-902"))
            self.assertFalse((root / "agent-system/runtime").exists())

            compiler = ContextCompiler(root)
            found_path, document = compiler._find_task_contract("DEMO-902")

            self.assertEqual(found_path, "agent-system/work/DEMO/tasks/DEMO-902.json")
            self.assertEqual(document["identity"]["task_id"], "DEMO-902")

    def test_no_contract_outside_runtime_raises_planning_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            _make_repo(root)
            runtime_only_path = root / "agent-system/runtime/worktrees/attempt-1/DEMO-903.json"
            _write_json(runtime_only_path, _task_contract("DEMO-903"))

            compiler = ContextCompiler(root)
            with self.assertRaises(PlanningError) as raised:
                compiler._find_task_contract("DEMO-903")

            self.assertIn("task contract file was not found", str(raised.exception))

    def test_discovery_never_opens_files_under_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            _make_repo(root)
            real_path = root / "agent-system/work/DEMO/tasks/DEMO-904.json"
            _write_json(real_path, _task_contract("DEMO-904"))
            runtime_path = root / "agent-system/runtime/worktrees/attempt-1/DEMO-904.json"
            _write_json(runtime_path, _task_contract("DEMO-904"))

            compiler = ContextCompiler(root)

            opened: list[str] = []
            original_read_text = pathlib.Path.read_text

            def _tracking_read_text(self: pathlib.Path, *args: object, **kwargs: object) -> str:
                opened.append(self.as_posix())
                return original_read_text(self, *args, **kwargs)

            with mock.patch.object(pathlib.Path, "read_text", _tracking_read_text):
                found_path, _document = compiler._find_task_contract("DEMO-904")

            self.assertEqual(found_path, "agent-system/work/DEMO/tasks/DEMO-904.json")
            self.assertFalse(
                any("agent-system/runtime" in opened_path for opened_path in opened)
            )


if __name__ == "__main__":
    unittest.main()
