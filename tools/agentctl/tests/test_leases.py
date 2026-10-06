"""Persistent lease, fencing, expiry, and orphan-evidence tests."""

from __future__ import annotations

import copy
import json
import pathlib
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone

from agentctl.approval import ApprovalManager, create_approval
from agentctl.errors import ContractStale, PermissionViolation, PlanningError
from agentctl.locks import semantic_lock_claims
from agentctl.scheduler import Scheduler, TaskSpec, task_approval_scope
from agentctl.store import EventStore


def _repo_root() -> pathlib.Path:
    output = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()
    return pathlib.Path(output)


class LeaseTests(unittest.TestCase):
    root = _repo_root()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = EventStore(
            pathlib.Path(self.temporary.name) / "runtime.sqlite3",
            repository_root=self.root,
        )
        self.scheduler = Scheduler(self.store)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _spec(self, task_id: str = "TASK-1") -> TaskSpec:
        base_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.root, text=True
        ).strip()
        return TaskSpec(
            task_id=task_id,
            work_package_id="WP-1",
            contract_hash="1" * 64,
            context_hash="2" * 64,
            base_commit=base_commit,
            capability_class="standard_implementation",
            risk="low",
            enforcement_class="sandboxed_tools",
            writable_paths=(f"docs/{task_id}.md",),
            prohibited_paths=("AppleBooks/**",),
            semantic_locks={},
            maximum_attempts=3,
            maximum_artifact_corrections=1,
        )

    def _register(self, task_id: str = "TASK-1") -> TaskSpec:
        spec = self._spec(task_id)
        self.scheduler.register_task(spec)
        return spec

    def _manifest(self, lease, spec: TaskSpec) -> dict:
        return {
            "artifact_type": "implementation-manifest",
            "task_id": spec.task_id,
            "attempt_id": lease.attempt_id,
            "lease_id": lease.lease_id,
            "lease_epoch": lease.epoch,
            "fencing_token": lease.fencing_token,
            "worker_role": spec.capability_class,
            "provider": "fixture-provider",
            "model": "fixture-model",
            "model_version": "fixture-version",
            "contract_hash": spec.contract_hash,
            "context_hash": spec.context_hash,
            "base_commit": spec.base_commit,
            "result_commit": "4" * 40,
            "files_changed": [
                {"path": spec.writable_paths[0], "change_type": "modified"}
            ],
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

    def test_epoch_strictly_increases_across_three_dispatches(self) -> None:
        self._register()
        epochs = []
        for index in range(3):
            lease = self.scheduler.dispatch("TASK-1", worker_identity=f"worker-{index}")
            epochs.append(lease.epoch)
            if index < 2:
                self.scheduler.expire_lease("TASK-1", force=True)
        self.assertEqual(epochs, [1, 2, 3])
        self.assertEqual(len(set(epochs)), 3)

    def test_expiry_releases_locks_but_preserves_attempt_and_worktree(self) -> None:
        spec = self._spec()
        spec = spec.with_semantic_locks(
            semantic_lock_claims(["repo:path-layout"], self.root)
        )
        self.scheduler.register_task(spec)
        lease = self.scheduler.dispatch("TASK-1", worker_identity="worker")
        self.scheduler.expire_lease("TASK-1", force=True)

        attempt = self.store.rows(
            "SELECT state, worktree_path FROM attempts WHERE attempt_id = ?",
            (lease.attempt_id,),
        )[0]
        self.assertEqual(attempt["state"], "expired")
        self.assertEqual(attempt["worktree_path"], lease.worktree_path)
        self.assertEqual(
            self.store.scalar(
                "SELECT COUNT(*) FROM semantic_locks WHERE owner_task_id = ?",
                ("TASK-1",),
            ),
            0,
        )
        self.assertEqual(self.store.status("TASK-1")["state"], "ready")

    def test_late_submission_is_orphan_evidence_without_state_advancement(self) -> None:
        spec = self._register()
        lease = self.scheduler.dispatch("TASK-1", worker_identity="worker")
        self.scheduler.expire_lease("TASK-1", force=True)
        with self.assertRaises(ContractStale):
            self.scheduler.ingest_manifest("TASK-1", self._manifest(lease, spec))
        status = self.store.status("TASK-1")
        self.assertEqual(status["state"], "ready")
        self.assertEqual(status["orphan_evidence_count"], 1)
        self.assertEqual(
            self.store.scalar("SELECT COUNT(*) FROM artifacts WHERE integrable = 0"), 1
        )

    def test_first_schema_error_uses_correction_not_attempt_or_lease_time(self) -> None:
        spec = self._register()
        lease = self.scheduler.dispatch("TASK-1", worker_identity="worker")
        self.scheduler.acknowledge("TASK-1", spec.context_hash)
        before = self.store.rows(
            "SELECT attempts_remaining, artifact_corrections_remaining FROM budgets "
            "WHERE scope_type = 'task' AND scope_id = ?",
            ("TASK-1",),
        )[0]

        first = self.scheduler.handle_schema_error("TASK-1", [{"keyword": "required"}])
        after = self.store.rows(
            "SELECT attempts_remaining, artifact_corrections_remaining FROM budgets "
            "WHERE scope_type = 'task' AND scope_id = ?",
            ("TASK-1",),
        )[0]
        active_expiry = self.store.scalar(
            "SELECT expires_at FROM leases WHERE lease_id = ?", (lease.lease_id,)
        )
        self.assertEqual(first, "correction_pending")
        self.assertEqual(after["attempts_remaining"], before["attempts_remaining"])
        self.assertEqual(after["artifact_corrections_remaining"], 0)
        self.assertEqual(active_expiry, lease.expires_at)
        self.assertEqual(self.store.status("TASK-1")["state"], "running")

        second = self.scheduler.handle_schema_error("TASK-1", [{"keyword": "required"}])
        self.assertEqual(second, "attempt_failed")
        self.assertEqual(self.store.status("TASK-1")["state"], "repair_required")

    def test_each_integrity_mismatch_revokes_lease_and_emits_permission_violation(self) -> None:
        mutations = {
            "fencing": lambda manifest: manifest.update(fencing_token="wrong"),
            "contract": lambda manifest: manifest.update(contract_hash="9" * 64),
            "context": lambda manifest: manifest.update(context_hash="9" * 64),
            "provenance": lambda manifest: manifest.update(base_commit="9" * 40),
            "prohibited_path": lambda manifest: manifest.update(
                files_changed=[{"path": "AppleBooks/secret.swift", "change_type": "modified"}]
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                task_id = f"TASK-{name}"
                spec = self._register(task_id)
                lease = self.scheduler.dispatch(task_id, worker_identity="worker")
                self.scheduler.acknowledge(task_id, spec.context_hash)
                manifest = self._manifest(lease, spec)
                mutate(manifest)
                with self.assertRaises(PermissionViolation):
                    self.scheduler.ingest_manifest(task_id, manifest)
                self.assertEqual(
                    self.store.scalar(
                        "SELECT active FROM leases WHERE lease_id = ?", (lease.lease_id,)
                    ),
                    0,
                )
                payload = json.loads(
                    self.store.rows(
                        "SELECT payload_json FROM events WHERE task_id = ? ORDER BY sequence DESC LIMIT 1",
                        (task_id,),
                    )[0]["payload_json"]
                )
                self.assertEqual(payload["reason_code"], "permission_violation")

    def test_committed_stale_fencing_fixture_is_rejected_with_seeded_store(self) -> None:
        spec = TaskSpec(
            **{
                **self._spec("FIXTURE-TASK").__dict__,
                "contract_hash": "1" * 64,
                "context_hash": "2" * 64,
                "base_commit": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=self.root, text=True
                ).strip(),
            }
        )
        self.scheduler.register_task(spec)
        lease = self.scheduler.dispatch("FIXTURE-TASK", worker_identity="fixture-worker")
        self.scheduler.acknowledge("FIXTURE-TASK", spec.context_hash)
        fixture_path = (
            self.root
            / "agent-system"
            / "examples"
            / "invalid"
            / "stateful"
            / "07-implementation-manifest-stale-fencing.json"
        )
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        fixture["attempt_id"] = lease.attempt_id
        fixture["lease_id"] = lease.lease_id
        fixture["base_commit"] = spec.base_commit
        with self.assertRaises(PermissionViolation):
            self.scheduler.ingest_manifest("FIXTURE-TASK", fixture)

    def test_privileged_task_cannot_lease_without_registered_bound_approval(self) -> None:
        key = b"p" * 32
        policy = {"tiers": {"destructive_operation": "interactive_tty_with_key_unlock"}}
        approvals = ApprovalManager(self.store, key=key, policy=policy)
        scheduler = Scheduler(self.store, approval_manager=approvals)
        spec = TaskSpec(
            **{
                **self._spec("PRIVILEGED-TASK").__dict__,
                "human_approval_ref": "MISSING-APPROVAL",
                "human_approval_action": "destructive_operation",
            }
        )
        scheduler.register_task(spec)
        with self.assertRaises(PlanningError):
            scheduler.dispatch("PRIVILEGED-TASK", worker_identity="worker")
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM leases"), 0)

    def test_bound_approval_is_consumed_in_the_lease_transaction(self) -> None:
        key = b"p" * 32
        policy = {"tiers": {"destructive_operation": "interactive_tty_with_key_unlock"}}
        approvals = ApprovalManager(self.store, key=key, policy=policy)
        scheduler = Scheduler(self.store, approval_manager=approvals)
        spec = TaskSpec(
            **{
                **self._spec("PRIVILEGED-TASK").__dict__,
                "human_approval_ref": "APPROVAL-PRIVILEGED-TASK",
                "human_approval_action": "destructive_operation",
            }
        )
        approval = create_approval(
            key=key,
            principal="owner",
            machine_identity="owner-machine",
            action="destructive_operation",
            target_type="task-contract",
            target_id=spec.task_id,
            target_hash=spec.contract_hash,
            scope=task_approval_scope(spec),
            reason="Authorize the exact privileged task contract",
            now=datetime.now(timezone.utc),
        )
        approval["approval_id"] = spec.human_approval_ref
        from agentctl.approval import seal_approval

        approval = seal_approval(approval, key)
        approvals.register(approval)
        scheduler.register_task(spec)
        lease = scheduler.dispatch("PRIVILEGED-TASK", worker_identity="worker")
        self.assertEqual(lease.epoch, 1)
        stored = json.loads(
            self.store.rows(
                "SELECT payload_json FROM artifacts WHERE artifact_id = ?",
                (spec.human_approval_ref,),
            )[0]["payload_json"]
        )
        self.assertIsNotNone(stored["consumed_at"])

    def test_wrong_approval_binding_never_issues_a_lease(self) -> None:
        key = b"p" * 32
        policy = {
            "tiers": {
                "destructive_operation": "interactive_tty_with_key_unlock",
                "protected_test_change": "interactive_tty_with_key_unlock",
            }
        }
        cases = {
            "action": {"action": "protected_test_change"},
            "target_type": {"target_type": "work-package"},
            "target_id": {"target_id": "ANOTHER-TASK"},
            "target_hash": {"target_hash": "9" * 64},
            "scope": {"scope": "Authorize a broader task"},
        }
        for index, (name, override) in enumerate(cases.items(), start=1):
            with self.subTest(name=name):
                task_id = f"PRIVILEGED-{index}"
                approvals = ApprovalManager(self.store, key=key, policy=policy)
                scheduler = Scheduler(self.store, approval_manager=approvals)
                spec = TaskSpec(
                    **{
                        **self._spec(task_id).__dict__,
                        "human_approval_ref": f"APPROVAL-{index}",
                        "human_approval_action": "destructive_operation",
                    }
                )
                values = {
                    "action": "destructive_operation",
                    "target_type": "task-contract",
                    "target_id": spec.task_id,
                    "target_hash": spec.contract_hash,
                    "scope": task_approval_scope(spec),
                    **override,
                }
                approval = create_approval(
                    key=key,
                    principal="owner",
                    machine_identity="owner-machine",
                    reason="Exercise a wrong approval binding",
                    now=datetime.now(timezone.utc),
                    **values,
                )
                approval["approval_id"] = spec.human_approval_ref
                from agentctl.approval import seal_approval

                approvals.register(seal_approval(approval, key))
                scheduler.register_task(spec)
                with self.assertRaises(PlanningError):
                    scheduler.dispatch(task_id, worker_identity="worker")
                self.assertEqual(
                    self.store.scalar(
                        "SELECT COUNT(*) FROM leases WHERE task_id = ?", (task_id,)
                    ),
                    0,
                )


if __name__ == "__main__":
    unittest.main()
