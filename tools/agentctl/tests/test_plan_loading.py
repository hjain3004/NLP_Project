"""Forward plan loading must create real schedulable task state."""

from __future__ import annotations

import copy
import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from agentctl.plan import PlanLoader, task_spec_from_contract
from agentctl.hashing import sha256_of_file
from agentctl.errors import ContractStale
from agentctl.store import EventStore


def _repo_root() -> pathlib.Path:
    return pathlib.Path(
        subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
    )


class PlanLoadingTests(unittest.TestCase):
    def test_task_contract_maps_to_scheduler_spec_without_losing_locks(self) -> None:
        root = _repo_root()
        contract = json.loads(
            (root / "agent-system/examples/valid/02-task-contract.json").read_text()
        )
        contract = copy.deepcopy(contract)
        contract["scope"]["semantic_locks"] = ["docs:generated-indexes"]
        contract["dependencies"]["consumes"] = ["contract:core-domain-v1@1"]
        spec = task_spec_from_contract(contract, context_hash="a" * 64)
        self.assertEqual(spec.task_id, "EX-DOC-001")
        self.assertEqual(spec.context_hash, "a" * 64)
        self.assertEqual(spec.writable_paths, ("agent-system/examples/README.md",))
        self.assertEqual(set(spec.semantic_locks), {"docs:generated-indexes"})
        self.assertEqual(
            spec.semantic_locks["docs:generated-indexes"],
            sha256_of_file(root / "agent-system/work/P0/gates/G0-criteria.json"),
        )
        self.assertEqual(
            spec.interface_pins,
            {"contract:core-domain-v1": "1"},
        )
        self.assertEqual(
            spec.deterministic_checks,
            tuple(contract["verification"]["deterministic_checks"]),
        )

    def test_plan_validation_is_idempotent_and_context_hashes_are_reproducible(self) -> None:
        root = _repo_root()
        plan = root / "agent-system/work/DRY-RUN/work-package.json"
        with tempfile.TemporaryDirectory() as temporary:
            store = EventStore(
                pathlib.Path(temporary) / "runtime.sqlite3",
                repository_root=root,
            )
            loader = PlanLoader(root, store)
            first = loader.validate(plan)
            second = loader.validate(plan)
        self.assertEqual(first, second)
        self.assertEqual(len(first["tasks"]), 8)

    def test_dispatch_rejects_a_changed_pinned_semantic_lock_source(self) -> None:
        source_root = _repo_root()
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary) / "repository"
            subprocess.run(
                ["git", "clone", "--quiet", "--shared", str(source_root), str(root)],
                check=True,
            )
            shutil.copy2(
                source_root / "agent-system/policies/semantic-locks.json",
                root / "agent-system/policies/semantic-locks.json",
            )
            store = EventStore(
                pathlib.Path(temporary) / "runtime.sqlite3", repository_root=root
            )
            loader = PlanLoader(root, store)
            loader.validate(root / "agent-system/work/DRY-RUN/work-package.json")
            protected = root / "agent-system/work/P0/gates/G0-criteria.json"
            protected.write_text(protected.read_text() + "\n", encoding="utf-8")
            with self.assertRaises(ContractStale):
                loader.scheduler.dispatch("DRY-007", worker_identity="worker")

    def test_completed_bootstrap_record_validates_without_forward_tasks(self) -> None:
        root = _repo_root()
        with tempfile.TemporaryDirectory() as temporary:
            store = EventStore(
                pathlib.Path(temporary) / "runtime.sqlite3", repository_root=root
            )
            result = PlanLoader(root, store).validate(
                root / "agent-system/work/P0/work-package.json"
            )
        self.assertEqual(result["work_package_id"], "P0-F")
        self.assertEqual(result["status"], "validated_bootstrap_record")
        self.assertEqual(result["tasks"], [])


if __name__ == "__main__":
    unittest.main()
