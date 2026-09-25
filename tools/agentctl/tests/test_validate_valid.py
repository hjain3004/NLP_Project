"""Acceptance tests for stateless artifact validation."""

from __future__ import annotations

import copy
import json
import pathlib
import subprocess
import tempfile
import unittest

from agentctl.errors import InvalidArtifact
from agentctl.hashing import sha256_of_json
from agentctl.validate import validate_artifact


def _repo_root() -> pathlib.Path:
    output = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()
    return pathlib.Path(output)


def _rehash_task_contract(document: dict) -> None:
    identity = document["identity"]
    old_hash = identity.pop("contract_hash", None)
    try:
        identity["contract_hash"] = sha256_of_json(document)
    except Exception:
        if old_hash is not None:
            identity["contract_hash"] = old_hash
        raise


class ValidArtifactTests(unittest.TestCase):
    root = _repo_root()
    valid_dir = root / "agent-system" / "examples" / "valid"

    def test_every_committed_valid_fixture_is_accepted(self) -> None:
        paths = sorted(self.valid_dir.glob("*.json"))
        self.assertEqual(len(paths), 8)
        for path in paths:
            with self.subTest(path=path.name):
                result = validate_artifact(path, repository_root=self.root)
                expected_type = json.loads(path.read_text())["artifact_type"]
                self.assertEqual(result["artifact_type"], expected_type)

    def test_task_contract_references_its_work_package(self) -> None:
        path = self.valid_dir / "02-task-contract.json"
        result = validate_artifact(path, repository_root=self.root)
        self.assertEqual(result["identity"]["work_package_id"], "EX-WP-DOC")

    def test_changed_task_contract_with_recomputed_hash_is_accepted(self) -> None:
        source = json.loads((self.valid_dir / "02-task-contract.json").read_text())
        changed = copy.deepcopy(source)
        changed["intent"]["rationale"] = "A different but still valid synthetic rationale"
        _rehash_task_contract(changed)

        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "task.json"
            path.write_text(json.dumps(changed), encoding="utf-8")
            result = validate_artifact(path, repository_root=self.root)
        self.assertEqual(result["identity"]["contract_hash"], changed["identity"]["contract_hash"])

    def test_task_contract_budget_lower_bounds_are_enforced_outside_schema(self) -> None:
        source = json.loads((self.valid_dir / "02-task-contract.json").read_text())
        cases = {
            "maximum_attempts": 0,
            "maximum_repair_cycles": -1,
            "maximum_artifact_corrections": -1,
        }
        for field, value in cases.items():
            with self.subTest(field=field):
                changed = copy.deepcopy(source)
                changed["budgets"][field] = value
                _rehash_task_contract(changed)
                with tempfile.TemporaryDirectory() as directory:
                    path = pathlib.Path(directory) / "task.json"
                    path.write_text(json.dumps(changed), encoding="utf-8")
                    with self.assertRaises(InvalidArtifact) as caught:
                        validate_artifact(path, repository_root=self.root)
                self.assertEqual(caught.exception.code, "budget_below_floor")


if __name__ == "__main__":
    unittest.main()
