"""Review pack minimization and five-dimensional independence tests."""

from __future__ import annotations

import json
import hashlib
import pathlib
import subprocess
import tempfile
import unittest

from agentctl.errors import PermissionViolation
from agentctl.review import ReviewIdentity, ReviewManager
from agentctl.scheduler import Scheduler, TaskSpec
from agentctl.store import EventStore


def _repo_root() -> pathlib.Path:
    return pathlib.Path(
        subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
    )


class ReviewIndependenceTests(unittest.TestCase):
    root = _repo_root()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.source_root = _repo_root()
        self.root = pathlib.Path(self.temporary.name) / "repository"
        subprocess.run(
            ["git", "clone", "--quiet", "--shared", str(self.source_root), str(self.root)],
            check=True,
        )
        self.store = EventStore(
            pathlib.Path(self.temporary.name) / "runtime.sqlite3",
            repository_root=self.root,
        )
        self.scheduler = Scheduler(self.store)
        self.manager = ReviewManager(self.store)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _candidate(self, task_id: str = "TASK-REVIEW") -> tuple[TaskSpec, object]:
        base_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.root, text=True
        ).strip()
        spec = TaskSpec(
            task_id=task_id,
            work_package_id="WP-REVIEW",
            contract_hash="1" * 64,
            context_hash="2" * 64,
            base_commit=base_commit,
            capability_class="standard_implementation",
            risk="low",
            enforcement_class="sandboxed_tools",
            writable_paths=(f"docs/{task_id}.md",),
            prohibited_paths=("AppleBooks/**",),
            semantic_locks={},
            maximum_attempts=1,
            maximum_artifact_corrections=1,
        )
        self.scheduler.register_task(spec)
        lease = self.scheduler.dispatch(task_id, worker_identity="author-session")
        self.scheduler.acknowledge(task_id, spec.context_hash)
        worktree = self.root / lease.worktree_path
        worktree.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "worktree", "add", "--quiet", "--detach", str(worktree), base_commit],
            cwd=self.root,
            check=True,
        )
        self.scheduler.ingest_manifest(
            task_id,
            {
                "artifact_type": "implementation-manifest",
                "task_id": task_id,
                "attempt_id": lease.attempt_id,
                "lease_id": lease.lease_id,
                "lease_epoch": lease.epoch,
                "fencing_token": lease.fencing_token,
                "worker_role": spec.capability_class,
                "provider": "author-provider",
                "model": "author-model",
                "model_version": "author-version",
                "contract_hash": spec.contract_hash,
                "context_hash": spec.context_hash,
                "base_commit": spec.base_commit,
                "result_commit": base_commit,
                "files_changed": [],
                "commands_executed": [],
                "acceptance_criteria_addressed": ["synthetic"],
                "assumptions": ["persuasive author claim"],
                "decisions": ["persuasive author decision"],
                "deviations": ["persuasive author deviation"],
                "discovered_dependencies": [],
                "unresolved_risks": [],
                "usage": {"input_tokens": 0, "output_tokens": 0, "latency_ms": 0, "cost_usd": 0},
                "termination_reason": "completed",
            },
        )
        author = ReviewIdentity(task_id, lease.attempt_id, "author-run", "author-session", spec.capability_class)
        self.manager.set_author_identity(task_id, author)
        self.scheduler.verify_candidate(task_id)
        return spec, lease

    def _reviewer(self) -> ReviewIdentity:
        return ReviewIdentity(
            "review-task", "review-attempt", "review-run", "review-session", "verifier"
        )

    def _verdict(self, spec: TaskSpec, lease: object, assignment: dict) -> dict:
        return {
            "artifact_type": "review-verdict",
            "review_id": f"{spec.task_id}-review-1",
            "task_id": spec.task_id,
            "attempt_id": lease.attempt_id,
            "reviewer_role": "verifier",
            "reviewer_provider": "review-provider",
            "reviewer_model": "review-model",
            "reviewer_session_id": "review-session",
            "reviewer_run_id": "review-run",
            "lease_id": assignment["lease_id"],
            "lease_epoch": assignment["lease_epoch"],
            "fencing_token": assignment["fencing_token"],
            "reviewed_result_commit": spec.base_commit,
            "tree_hash": subprocess.check_output(
                ["git", "rev-parse", f"{spec.base_commit}^{{tree}}"],
                cwd=self.root,
                text=True,
            ).strip(),
            "diff_hash": hashlib.sha256(b"").hexdigest(),
            "declared_base_commit": spec.base_commit,
            "contract_hash": spec.contract_hash,
            "context_hash": spec.context_hash,
            "verification_evidence_hash": self.store.scalar(
                "SELECT content_hash FROM artifacts WHERE artifact_id = ?",
                (f"verification:{spec.task_id}:{lease.attempt_id}",),
            ),
            "findings": [],
            "verdict": "no_blocking_findings",
            "independence_attestation": {
                "author_session_id": "author-session",
                "author_run_id": "author-run",
                "distinct": True,
            },
        }

    def test_first_pass_pack_omits_persuasive_author_summary(self) -> None:
        _spec, _lease = self._candidate()
        pack = self.manager.prepare("TASK-REVIEW")
        serialized = json.dumps(pack, sort_keys=True)
        self.assertNotIn("persuasive author", serialized)
        self.assertNotIn("implementation_manifest", pack)
        self.assertEqual(self.store.status("TASK-REVIEW")["state"], "independent_review")

    def test_each_of_five_identity_dimensions_independently_rejects_self_review(self) -> None:
        dimensions = (
            "task_id",
            "attempt_id",
            "provider_run_id",
            "model_session_id",
            "actor_role",
        )
        for dimension in dimensions:
            with self.subTest(dimension=dimension):
                self.tearDown()
                self.setUp()
                spec, lease = self._candidate(f"TASK-{dimension}")
                self.manager.prepare(spec.task_id)
                distinct = self._reviewer()
                values = distinct.__dict__.copy()
                author = self.manager.author_identity(spec.task_id)
                values[dimension] = getattr(author, dimension)
                reviewer = ReviewIdentity(**values)
                with self.assertRaises(PermissionViolation) as raised:
                    self.manager.assign(spec.task_id, reviewer)
                self.assertEqual(raised.exception.context["dimension"], dimension)
                self.assertEqual(self.store.status(spec.task_id)["state"], "independent_review")

    def test_independent_verdict_advances_to_integration_ready(self) -> None:
        spec, lease = self._candidate()
        self.manager.prepare(spec.task_id)
        assignment = self.manager.assign(spec.task_id, self._reviewer())
        result = self.manager.ingest(spec.task_id, self._verdict(spec, lease, assignment))
        self.assertEqual(result["state"], "integration_ready")
        self.assertEqual(self.store.status(spec.task_id)["state"], "integration_ready")

    def test_ingest_fails_closed_without_a_registered_reviewer_assignment(self) -> None:
        spec, lease = self._candidate()
        self.manager.prepare(spec.task_id)
        forged = {
            "lease_id": "forged-review-lease",
            "lease_epoch": 1,
            "fencing_token": "forged-review-token",
        }
        with self.assertRaises(PermissionViolation):
            self.manager.ingest(spec.task_id, self._verdict(spec, lease, forged))
        self.assertEqual(self.store.status(spec.task_id)["state"], "independent_review")

    def test_artifact_cannot_override_registered_reviewer_identity(self) -> None:
        spec, lease = self._candidate()
        self.manager.prepare(spec.task_id)
        assignment = self.manager.assign(spec.task_id, self._reviewer())
        verdict = self._verdict(spec, lease, assignment)
        verdict["reviewer_session_id"] = "forged-distinct-session"
        verdict["reviewer_run_id"] = "forged-distinct-run"
        with self.assertRaises(PermissionViolation):
            self.manager.ingest(spec.task_id, verdict)
        self.assertEqual(self.store.status(spec.task_id)["state"], "independent_review")

    def test_committed_false_attestation_fixture_is_rejected_against_seeded_identity(self) -> None:
        spec, lease = self._candidate("FIXTURE-TASK")
        self.manager.set_author_identity(
            spec.task_id,
            ReviewIdentity(spec.task_id, lease.attempt_id, "fixture-author-run", "fixture-author-session", "standard_implementation"),
        )
        self.manager.prepare(spec.task_id)
        fixture = json.loads(
            (self.source_root / "agent-system/examples/invalid/stateful/06-review-verdict-self-review.json").read_text(encoding="utf-8")
        )
        reviewer = ReviewIdentity(
            task_id=fixture["task_id"],
            attempt_id=fixture["attempt_id"],
            provider_run_id=fixture["reviewer_run_id"],
            model_session_id=fixture["reviewer_session_id"],
            actor_role=fixture["reviewer_role"],
        )
        with self.assertRaises(PermissionViolation) as caught:
            self.manager.assign(spec.task_id, reviewer)
        self.assertEqual(caught.exception.context["dimension"], "task_id")
        event = json.loads(
            self.store.rows(
                "SELECT payload_json FROM events WHERE task_id = ? ORDER BY sequence DESC LIMIT 1",
                (spec.task_id,),
            )[0]["payload_json"]
        )
        self.assertEqual(event["reason_code"], "self_review_rejected")


if __name__ == "__main__":
    unittest.main()
