"""Regression tests for trusted candidate, verification, and review bindings."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from agentctl.errors import ContractStale, IllegalTransition, PermissionViolation
from agentctl.review import ReviewIdentity, ReviewManager
from agentctl.scheduler import Scheduler, TaskSpec
from agentctl.store import EventStore


def _git(root: pathlib.Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


class TrustedCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name) / "repository"
        self.root.mkdir()
        source_root = pathlib.Path(
            subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"], text=True
            ).strip()
        )
        shutil.copytree(
            source_root / "agent-system" / "schemas",
            self.root / "agent-system" / "schemas",
        )
        shutil.copytree(
            source_root / "agent-system" / "policies",
            self.root / "agent-system" / "policies",
        )
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "fixture@example.invalid"],
            cwd=self.root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Fixture"], cwd=self.root, check=True
        )
        (self.root / "docs").mkdir()
        (self.root / "docs" / "candidate.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "--all"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=self.root, check=True)
        self.base = _git(self.root, "rev-parse", "HEAD")
        self.store = EventStore(
            pathlib.Path(self.temporary.name) / "runtime.sqlite3",
            repository_root=self.root,
        )
        self.scheduler = Scheduler(self.store)
        self.manager = ReviewManager(self.store)
        self.spec = TaskSpec(
            task_id="TASK-CANDIDATE",
            work_package_id="WP",
            contract_hash="1" * 64,
            context_hash="2" * 64,
            base_commit=self.base,
            capability_class="standard_implementation",
            risk="low",
            enforcement_class="sandboxed_tools",
            writable_paths=("docs/candidate.md",),
            prohibited_paths=("secrets/**",),
            semantic_locks={},
            maximum_attempts=3,
            maximum_artifact_corrections=1,
            deterministic_checks=("true",),
        )
        self.scheduler.register_task(self.spec)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _manifest(self, lease: object, result_commit: str) -> dict:
        return {
            "artifact_type": "implementation-manifest",
            "task_id": self.spec.task_id,
            "attempt_id": lease.attempt_id,
            "lease_id": lease.lease_id,
            "lease_epoch": lease.epoch,
            "fencing_token": lease.fencing_token,
            "worker_role": self.spec.capability_class,
            "provider": "fixture-provider",
            "model": "fixture-model",
            "model_version": "fixture-version",
            "contract_hash": self.spec.contract_hash,
            "context_hash": self.spec.context_hash,
            "base_commit": self.base,
            "result_commit": result_commit,
            "files_changed": [{"path": "docs/candidate.md", "change_type": "modified"}],
            "commands_executed": [],
            "acceptance_criteria_addressed": ["synthetic"],
            "assumptions": [],
            "decisions": [],
            "deviations": [],
            "discovered_dependencies": [],
            "unresolved_risks": [],
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "latency_ms": 0,
                "cost_usd": 0,
            },
            "termination_reason": "completed",
        }

    def _real_candidate(self, lease: object) -> str:
        worktree = self.root / lease.worktree_path
        worktree.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "worktree", "add", "--quiet", "--detach", str(worktree), self.base],
            cwd=self.root,
            check=True,
        )
        (worktree / "docs" / "candidate.md").write_text("candidate\n", encoding="utf-8")
        subprocess.run(["git", "add", "--all"], cwd=worktree, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "candidate"], cwd=worktree, check=True)
        return _git(worktree, "rev-parse", "HEAD")

    def test_candidate_ingest_requires_acknowledged_running_lease(self) -> None:
        lease = self.scheduler.dispatch(self.spec.task_id, worker_identity="author-session")
        with self.assertRaises(IllegalTransition):
            self.scheduler.ingest_manifest(
                self.spec.task_id, self._manifest(lease, "f" * 40)
            )
        self.assertEqual(self.store.status(self.spec.task_id)["state"], "leased")

    def test_candidate_ingest_rejects_nonexistent_result_commit(self) -> None:
        lease = self.scheduler.dispatch(self.spec.task_id, worker_identity="author-session")
        self.scheduler.acknowledge(self.spec.task_id, self.spec.context_hash)
        with self.assertRaises(PermissionViolation) as caught:
            self.scheduler.ingest_manifest(
                self.spec.task_id, self._manifest(lease, "f" * 40)
            )
        self.assertEqual(caught.exception.context["mismatch"], "provenance")
        self.assertEqual(self.store.status(self.spec.task_id)["state"], "repair_required")
        self.assertEqual(
            self.store.scalar(
                "SELECT active FROM leases WHERE lease_id = ?", (lease.lease_id,)
            ),
            0,
        )

    def test_trusted_candidate_binding_drives_verification_and_review(self) -> None:
        lease = self.scheduler.dispatch(self.spec.task_id, worker_identity="author-session")
        self.scheduler.acknowledge(self.spec.task_id, self.spec.context_hash)
        result_commit = self._real_candidate(lease)
        self.scheduler.ingest_manifest(
            self.spec.task_id, self._manifest(lease, result_commit)
        )
        with self.assertRaises(IllegalTransition):
            self.manager.prepare(self.spec.task_id)

        verification = self.scheduler.verify_candidate(self.spec.task_id)
        self.assertEqual(verification["state"], "independent_review")
        pack = self.manager.prepare(self.spec.task_id)
        binding = pack["candidate_binding"]
        self.assertEqual(binding["result_commit"], result_commit)
        self.assertEqual(binding["tree_hash"], _git(self.root, "rev-parse", f"{result_commit}^{{tree}}"))
        self.assertEqual(len(binding["diff_hash"]), 64)
        self.assertEqual(
            binding["verification_evidence_hash"],
            verification["verification_evidence_hash"],
        )

    def test_review_rejects_altered_trusted_binding_and_expired_assignment(self) -> None:
        lease = self.scheduler.dispatch(self.spec.task_id, worker_identity="author-session")
        self.scheduler.acknowledge(self.spec.task_id, self.spec.context_hash)
        result_commit = self._real_candidate(lease)
        self.scheduler.ingest_manifest(
            self.spec.task_id, self._manifest(lease, result_commit)
        )
        self.manager.set_author_identity(
            self.spec.task_id,
            ReviewIdentity(
                self.spec.task_id,
                lease.attempt_id,
                "author-run",
                "author-session",
                "standard_implementation",
            ),
        )
        verification = self.scheduler.verify_candidate(self.spec.task_id)
        pack = self.manager.prepare(self.spec.task_id)
        reviewer = ReviewIdentity(
            "REVIEW-TASK", "REVIEW-ATTEMPT", "review-run", "review-session", "verifier"
        )
        assignment = self.manager.assign(self.spec.task_id, reviewer)
        binding = pack["candidate_binding"]
        verdict = {
            "artifact_type": "review-verdict",
            "review_id": "REVIEW-1",
            "task_id": self.spec.task_id,
            "attempt_id": lease.attempt_id,
            "reviewer_role": reviewer.actor_role,
            "reviewer_provider": "provider",
            "reviewer_model": "model",
            "reviewer_session_id": reviewer.model_session_id,
            "reviewer_run_id": reviewer.provider_run_id,
            "lease_id": assignment["lease_id"],
            "lease_epoch": assignment["lease_epoch"],
            "fencing_token": assignment["fencing_token"],
            "reviewed_result_commit": binding["result_commit"],
            "tree_hash": "f" * 40,
            "diff_hash": binding["diff_hash"],
            "declared_base_commit": self.base,
            "contract_hash": self.spec.contract_hash,
            "context_hash": self.spec.context_hash,
            "verification_evidence_hash": verification["verification_evidence_hash"],
            "findings": [],
            "verdict": "no_blocking_findings",
            "independence_attestation": {
                "author_session_id": "author-session",
                "author_run_id": "author-run",
                "distinct": True,
            },
        }
        with self.assertRaises(ContractStale):
            self.manager.ingest(self.spec.task_id, verdict)

        assignment_id = f"review-assignment:{self.spec.task_id}:{assignment['lease_id']}"
        row = self.store.rows(
            "SELECT payload_json FROM artifacts WHERE artifact_id = ?", (assignment_id,)
        )[0]
        payload = json.loads(row["payload_json"])
        payload["expires_at"] = "2000-01-01T00:00:00Z"
        with self.store._connection() as connection:  # test-only corruption fixture
            connection.execute(
                "UPDATE artifacts SET payload_json = ? WHERE artifact_id = ?",
                (json.dumps(payload, sort_keys=True, separators=(",", ":")), assignment_id),
            )
        verdict["tree_hash"] = binding["tree_hash"]
        with self.assertRaises(PermissionViolation) as caught:
            self.manager.ingest(self.spec.task_id, verdict)
        self.assertEqual(caught.exception.context["dimension"], "lease_expired")


if __name__ == "__main__":
    unittest.main()
