"""Acceptance tests for stateless artifact validation."""

from __future__ import annotations

import copy
import hashlib
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

    def test_task_contract_rejects_unregistered_semantic_lock(self) -> None:
        source = json.loads((self.valid_dir / "02-task-contract.json").read_text())
        source["scope"]["semantic_locks"] = ["unknown:unregistered-lock"]
        _rehash_task_contract(source)
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "task.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaises(InvalidArtifact) as caught:
                validate_artifact(path, repository_root=self.root)
        self.assertEqual(caught.exception.code, "semantic_lock_unregistered")

    def test_retry_manifest_may_reuse_an_identical_compiled_context(self) -> None:
        source = json.loads((self.valid_dir / "04-implementation-manifest.json").read_text())
        source["attempt_id"] = "EX-DOC-001-A2"
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "retry-manifest.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            result = validate_artifact(path, repository_root=self.root)
        self.assertEqual(result["attempt_id"], "EX-DOC-001-A2")

    def test_implementation_review_and_integration_fixtures_bind_one_exact_git_diff(self) -> None:
        contract = json.loads((self.valid_dir / "02-task-contract.json").read_text())
        manifest = json.loads((self.valid_dir / "04-implementation-manifest.json").read_text())
        review = json.loads((self.valid_dir / "05-review-verdict.json").read_text())
        integration = json.loads((self.valid_dir / "06-integration-manifest.json").read_text())
        base = contract["identity"]["base_commit"]
        result = manifest["result_commit"]
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", base, result],
            cwd=self.root,
            check=True,
        )
        changed = subprocess.check_output(
            ["git", "diff", "--name-status", base, result], cwd=self.root, text=True
        ).strip()
        self.assertEqual(changed, "M\tagent-system/examples/README.md")
        diff = subprocess.check_output(
            ["git", "diff", "--binary", "--full-index", base, result], cwd=self.root
        )
        diff_hash = hashlib.sha256(diff).hexdigest()
        tree_hash = subprocess.check_output(
            ["git", "rev-parse", f"{result}^{{tree}}"], cwd=self.root, text=True
        ).strip()
        self.assertEqual(review["reviewed_result_commit"], result)
        self.assertEqual(review["declared_base_commit"], base)
        self.assertEqual(review["tree_hash"], tree_hash)
        self.assertEqual(review["diff_hash"], diff_hash)
        self.assertEqual(integration["candidate_result_commit"], result)
        self.assertEqual(integration["integration_base_commit"], base)
        self.assertEqual(integration["integration_commit"], result)
        self.assertEqual(integration["reviewed_diff_hash"], diff_hash)


if __name__ == "__main__":
    unittest.main()
