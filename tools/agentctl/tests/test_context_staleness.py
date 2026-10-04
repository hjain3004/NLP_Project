"""Context compilation, exclusion, invalidation, and rebuild tests."""

from __future__ import annotations

import pathlib
import subprocess
import tempfile
import unittest

from agentctl.context import (
    CONTEXT_INPUTS,
    EXCLUDED_BY_POLICY,
    ContextCompiler,
    assert_context_hash,
)
from agentctl.errors import ContractStale, PlanningError
from agentctl.scheduler import Scheduler, TaskSpec
from agentctl.store import EventStore


def _repo_root() -> pathlib.Path:
    return pathlib.Path(
        subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
    )


class ContextCompilationTests(unittest.TestCase):
    root = _repo_root()

    def _inputs(self) -> dict[str, object]:
        return {
            "authority_excerpt": "AGENTS.md",
            "role_contract": "agent-system/roles/mechanical-executor.md",
            "task_contract": "agent-system/examples/valid/02-task-contract.json",
            "decisions": ["docs/decisions/ADR-0001-deterministic-multi-model-control-plane.md"],
            "consumed_interfaces": [],
            "dependency_manifests": [],
            "failure_lessons": [],
            "base_commit_summary": "agent-system/work/P0/evidence/migration.json",
            "permissions": "agent-system/policies/permissions.json",
            "budgets_and_output_schema": "agent-system/schemas/implementation-manifest.schema.json",
        }

    def test_manifest_has_exactly_ten_ordered_inputs_and_eight_exclusions(self) -> None:
        compiler = ContextCompiler(self.root)
        manifest = compiler.compile(
            task_id="TASK-CTX",
            attempt_id="TASK-CTX-attempt-1",
            role="mechanical_execution",
            inputs=self._inputs(),
        )
        self.assertEqual(tuple(manifest["inputs"]), CONTEXT_INPUTS)
        self.assertEqual(tuple(manifest["excluded_by_policy"]), EXCLUDED_BY_POLICY)
        for value in manifest["inputs"].values():
            entries = value if isinstance(value, list) else [value]
            for entry in entries:
                self.assertEqual(set(entry), {"path", "sha256", "bytes"})
        self.assertRegex(manifest["context_hash"], r"^[0-9a-f]{64}$")

    def test_returned_artifact_must_echo_the_compiled_context_hash(self) -> None:
        artifact = {"context_hash": "a" * 64}
        assert_context_hash("a" * 64, artifact)
        with self.assertRaises(ContractStale):
            assert_context_hash("b" * 64, artifact)

    def test_compiler_rejects_explicitly_excluded_and_disallowed_paths(self) -> None:
        compiler = ContextCompiler(self.root)
        for path in ("docs/archive/README.md", "CLAUDE.md"):
            with self.subTest(path=path):
                inputs = self._inputs()
                inputs["decisions"] = [path]
                with self.assertRaises(PlanningError):
                    compiler.compile(
                        task_id="TASK-CTX",
                        attempt_id="TASK-CTX-attempt-1",
                        role="mechanical_execution",
                        inputs=inputs,
                    )

    def test_interface_bump_stales_consumer_and_rebuild_changes_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = EventStore(
                pathlib.Path(temporary) / "runtime.sqlite3",
                repository_root=self.root,
            )
            scheduler = Scheduler(store)
            old_hash = "2" * 64
            scheduler.register_task(
                TaskSpec(
                    task_id="TASK-CTX",
                    work_package_id="WP-CTX",
                    contract_hash="1" * 64,
                    context_hash=old_hash,
                    base_commit="3" * 40,
                    capability_class="mechanical_execution",
                    risk="low",
                    enforcement_class="sandboxed_tools",
                    writable_paths=("docs/context.md",),
                    prohibited_paths=("AppleBooks/**",),
                    semantic_locks={},
                    maximum_attempts=1,
                    maximum_artifact_corrections=1,
                    interface_pins={"contract:core-domain-v1": "a" * 64},
                )
            )
            self.assertEqual(
                scheduler.bump_interface("contract:core-domain-v1"), ("TASK-CTX",)
            )
            self.assertEqual(store.status("TASK-CTX")["state"], "stale")

            manifest = ContextCompiler(self.root, store).rebuild(
                "TASK-CTX", role="mechanical_execution", inputs=self._inputs()
            )
            self.assertNotEqual(manifest["context_hash"], old_hash)
            self.assertEqual(store.status("TASK-CTX")["state"], "validated")
            self.assertEqual(
                store.scalar("SELECT context_hash FROM tasks WHERE task_id = ?", ("TASK-CTX",)),
                manifest["context_hash"],
            )


if __name__ == "__main__":
    unittest.main()
