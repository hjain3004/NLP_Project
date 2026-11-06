"""Rejection tests for stateless artifact validation."""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from agentctl.cli import main
from agentctl.errors import InvalidArtifact, SchemaUndeterminable
from agentctl.hashing import sha256_of_json
from agentctl.validate import validate_artifact


def _repo_root() -> pathlib.Path:
    output = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()
    return pathlib.Path(output)


def _rehash_task_contract(document: dict) -> None:
    document["identity"].pop("contract_hash", None)
    document["identity"]["contract_hash"] = sha256_of_json(document)


class InvalidArtifactTests(unittest.TestCase):
    root = _repo_root()
    valid_task_path = root / "agent-system" / "examples" / "valid" / "02-task-contract.json"
    invalid_dir = root / "agent-system" / "examples" / "invalid" / "static"

    def _task(self) -> dict:
        return json.loads(self.valid_task_path.read_text(encoding="utf-8"))

    def _validate_changed_task(self, changed: dict) -> InvalidArtifact:
        _rehash_task_contract(changed)
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "task.json"
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaises(InvalidArtifact) as caught:
                validate_artifact(path, repository_root=self.root)
        return caught.exception

    def test_static_must_fail_fixtures_have_their_intended_distinct_codes(self) -> None:
        expected = {
            "01-task-contract-missing-required-field.json": "schema_validation_failed",
            "09-task-contract-missing-preferred-worker.json": "schema_validation_failed",
            "10-task-contract-missing-required-skills.json": "schema_validation_failed",
            "11-task-contract-missing-provider-diversity.json": "schema_validation_failed",
            "02-task-contract-overlapping-write-prohibit.json": "overlapping_write_prohibit",
            "03-task-contract-path-traversal.json": "path_traversal",
            "04-task-contract-secret-access-true.json": "secret_access_true",
            "05-task-contract-attempts-over-ceiling.json": "attempts_over_ceiling",
            "08-integration-manifest-patch-altered.json": "patch_altered",
        }
        self.assertEqual({path.name for path in self.invalid_dir.glob("*.json")}, set(expected))
        for name, code in expected.items():
            with self.subTest(path=name):
                with self.assertRaises(InvalidArtifact) as caught:
                    validate_artifact(self.invalid_dir / name, repository_root=self.root)
                self.assertEqual(caught.exception.code, code)

    def test_missing_or_unknown_artifact_type_is_schema_undeterminable(self) -> None:
        for document in ({"not_an_artifact": 1}, {"artifact_type": "unknown"}):
            with self.subTest(document=document):
                with tempfile.TemporaryDirectory() as directory:
                    path = pathlib.Path(directory) / "artifact.json"
                    path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(SchemaUndeterminable):
                        validate_artifact(path, repository_root=self.root)

    def test_cli_names_schema_undeterminable_error_class(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "artifact.json"
            path.write_text('{"not_an_artifact": 1}', encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = main(["validate", str(path)])
        self.assertNotEqual(exit_code, 0)
        self.assertIn("SchemaUndeterminable", stderr.getvalue())

    def test_path_rules_reject_broad_absolute_traversal_and_unnormalized_paths(self) -> None:
        for value in ("**", "/tmp/file", "docs/../secrets", "docs//file"):
            with self.subTest(value=value):
                changed = self._task()
                changed["scope"]["writable_paths"] = [value]
                error = self._validate_changed_task(changed)
                self.assertEqual(error.code, "path_traversal")

    def test_unbound_base_is_only_permitted_for_proposed_contract(self) -> None:
        changed = self._task()
        changed["identity"]["base_commit"] = "unbound"
        changed["identity"]["status"] = "validated"
        error = self._validate_changed_task(changed)
        self.assertEqual(error.code, "unbound_base_commit")

    def test_critical_task_cannot_use_unsandboxed_tools(self) -> None:
        changed = self._task()
        changed["routing"]["risk"] = "critical"
        changed["permissions"]["enforcement_class"] = "unsandboxed_tools"
        error = self._validate_changed_task(changed)
        self.assertEqual(error.code, "critical_unsandboxed")

    def test_required_acceptance_evidence_must_not_be_empty(self) -> None:
        changed = self._task()
        changed["verification"]["deterministic_checks"] = []
        error = self._validate_changed_task(changed)
        self.assertEqual(error.code, "acceptance_evidence_missing")

    def test_destructive_task_requires_matching_human_approval_binding(self) -> None:
        changed = self._task()
        changed["permissions"]["destructive_commands"] = True
        error = self._validate_changed_task(changed)
        self.assertEqual(error.code, "human_approval_required")

        changed["permissions"]["human_approval_ref"] = "APPROVAL-DESTRUCTIVE-1"
        changed["permissions"]["required_human_approval_action"] = "protected_test_change"
        error = self._validate_changed_task(changed)
        self.assertEqual(error.code, "human_approval_wrong_action")

    def test_protected_test_task_requires_matching_human_approval_binding(self) -> None:
        changed = self._task()
        changed["permissions"]["may_modify_tests"] = "protected_with_approval"
        error = self._validate_changed_task(changed)
        self.assertEqual(error.code, "human_approval_required")

        changed["permissions"]["human_approval_ref"] = "APPROVAL-PROTECTED-1"
        changed["permissions"]["required_human_approval_action"] = "destructive_operation"
        error = self._validate_changed_task(changed)
        self.assertEqual(error.code, "human_approval_wrong_action")

    def test_context_exclusions_are_the_exact_policy_set(self) -> None:
        source = self.root / "agent-system/examples/valid/03-context-manifest.json"
        changed = json.loads(source.read_text(encoding="utf-8"))
        changed["excluded_by_policy"] = ["prd"] * 8
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "context.json"
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaises(InvalidArtifact) as caught:
                validate_artifact(path, repository_root=self.root)
        self.assertEqual(caught.exception.code, "context_exclusions_invalid")

    def test_task_dependency_must_resolve(self) -> None:
        changed = self._task()
        changed["dependencies"]["requires"] = ["TASK-DOES-NOT-EXIST"]
        error = self._validate_changed_task(changed)
        self.assertEqual(error.code, "dependency_unresolved")

    def test_ids_and_versions_are_nonempty_and_positive(self) -> None:
        id_changed = self._task()
        id_changed["identity"]["task_id"] = ""
        self.assertEqual(self._validate_changed_task(id_changed).code, "invalid_identifier")

        version_changed = self._task()
        version_changed["identity"]["contract_version"] = 0
        self.assertEqual(self._validate_changed_task(version_changed).code, "invalid_contract_version")

    def test_self_hash_mismatch_is_rejected_last(self) -> None:
        changed = self._task()
        changed["intent"]["rationale"] = "tampered without updating the hash"
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "task.json"
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaises(InvalidArtifact) as caught:
                validate_artifact(path, repository_root=self.root)
        self.assertEqual(caught.exception.code, "artifact_hash_mismatch")

    def test_task_contract_mandatory_identity_intent_and_dependency_fields(self) -> None:
        cases = (
            ("identity", "expires_at"),
            ("intent", "relevant_decisions"),
            ("dependencies", "integration_order"),
            ("dependencies", "invalidation_triggers"),
        )
        for section, field in cases:
            with self.subTest(section=section, field=field):
                changed = self._task()
                del changed[section][field]
                error = self._validate_changed_task(changed)
                self.assertEqual(error.code, "schema_validation_failed")

    def test_leased_review_and_integration_artifacts_require_fencing_bindings(self) -> None:
        valid_dir = self.root / "agent-system/examples/valid"
        for filename in ("05-review-verdict.json", "06-integration-manifest.json"):
            for field in ("lease_id", "lease_epoch", "fencing_token"):
                with self.subTest(filename=filename, field=field):
                    changed = json.loads((valid_dir / filename).read_text())
                    del changed[field]
                    with tempfile.TemporaryDirectory() as directory:
                        path = pathlib.Path(directory) / filename
                        path.write_text(json.dumps(changed), encoding="utf-8")
                        with self.assertRaises(InvalidArtifact) as caught:
                            validate_artifact(path, repository_root=self.root)
                    self.assertEqual(caught.exception.code, "schema_validation_failed")

    def test_findings_require_file_location_and_reviewer_confidence(self) -> None:
        expected = {"file", "location", "reviewer_confidence"}
        for filename in (
            "review-verdict.schema.json",
            "architecture-audit.schema.json",
        ):
            with self.subTest(filename=filename):
                schema = json.loads(
                    (self.root / "agent-system/schemas" / filename).read_text()
                )
                required = set(schema["properties"]["findings"]["items"]["required"])
                self.assertLessEqual(expected, required)

    def test_work_package_requires_bootstrap_and_plan_bindings(self) -> None:
        schema = json.loads(
            (self.root / "agent-system/schemas/work-package.schema.json").read_text()
        )
        self.assertLessEqual({"bootstrap_phase", "plan_hash"}, set(schema["required"]))

    def test_cross_artifact_hash_link_tampering_is_rejected(self) -> None:
        cases = (
            ("04-implementation-manifest.json", "contract_hash"),
            ("05-review-verdict.json", "context_hash"),
            ("06-integration-manifest.json", "reviewed_diff_hash"),
            ("08-human-approval.json", "target_hash"),
        )
        valid_dir = self.root / "agent-system" / "examples" / "valid"
        for filename, field in cases:
            with self.subTest(filename=filename, field=field):
                with tempfile.TemporaryDirectory() as directory:
                    fixture_dir = pathlib.Path(directory) / "valid"
                    shutil.copytree(valid_dir, fixture_dir)
                    path = fixture_dir / filename
                    changed = json.loads(path.read_text(encoding="utf-8"))
                    changed[field] = "0" * 64
                    path.write_text(json.dumps(changed), encoding="utf-8")
                    with self.assertRaises(InvalidArtifact) as caught:
                        validate_artifact(path, repository_root=self.root)
                self.assertEqual(caught.exception.code, "artifact_hash_mismatch")


if __name__ == "__main__":
    unittest.main()
