"""Synthetic, fail-closed tests for governance proposal application."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import shutil
import tempfile
import unittest
from agentctl.approval import SYNTHETIC_FIXTURE_KEY, build_approval, seal_approval
from agentctl.governance import GovernanceApplier
from agentctl.hashing import sha256_of_file, sha256_of_json
from agentctl.store import EventStore


ROOT = pathlib.Path(__file__).resolve().parents[3]
PROPOSAL = ROOT / "agent-system/work/P0/proposals/governance-change-set.json"
APPROVAL_RELATIVE = pathlib.Path("agent-system/work/P0/approvals/synthetic-governance.json")
TARGETS = tuple(
    json.loads(PROPOSAL.read_text(encoding="utf-8"))["operations"]
)


class GovernanceApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.directory.name)
        for operation in TARGETS:
            source = ROOT / operation["path"]
            destination = self.root / operation["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        proposal_destination = self.root / "agent-system/work/P0/proposals/governance-change-set.json"
        proposal_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROPOSAL, proposal_destination)
        # The resolver/proposal validator deliberately reads the live policy.
        for relative in ("agent-system/policies/approval-principals.json", "agent-system/schemas/event.schema.json"):
            source = ROOT / relative
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        approval_path = self.root / APPROVAL_RELATIVE
        approval_path.parent.mkdir(parents=True, exist_ok=True)
        proposal = json.loads(PROPOSAL.read_text(encoding="utf-8"))
        self.approval = seal_approval(build_approval(
            principal="synthetic-fixture-principal",
            machine_identity="synthetic-fixture-machine",
            action="schema_commitment",
            target_type="governance-change-set",
            target_id=proposal["proposal_id"],
            target_hash=sha256_of_json(proposal),
            scope="Apply only the exact governance-change-set proposal operations",
            reason="Synthetic exact governance application",
            now=dt.datetime(2026, 10, 10, 7, 0, tzinfo=dt.timezone.utc),
            lifetime_seconds=3600,
            authentication_method="local_authentication",
        ), SYNTHETIC_FIXTURE_KEY)
        approval_path.write_text(json.dumps(self.approval, indent=2) + "\n", encoding="utf-8")
        self.store = EventStore(self.root / "runtime.sqlite3", repository_root=self.root)
        self.proposal_path = self.root / "agent-system/work/P0/proposals/governance-change-set.json"
        self.approval_path = approval_path

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _applier(self, **kwargs) -> GovernanceApplier:
        return GovernanceApplier(self.store, key=SYNTHETIC_FIXTURE_KEY, **kwargs)

    def _before(self) -> dict[str, bytes]:
        return {operation["path"]: (self.root / operation["path"]).read_bytes() for operation in TARGETS}

    def test_preflight_is_exact_ten_and_non_mutating(self) -> None:
        before = self._before()
        result = self._applier().preflight(self.proposal_path, self.approval_path)
        self.assertEqual(result["operation_count"], 10)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(before, self._before())
        self.assertEqual(self.store.event_count(), 0)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM artifacts"), 0)

    def test_success_applies_exact_payload_and_consumes_once(self) -> None:
        result = self._applier().apply(self.proposal_path, self.approval_path)
        self.assertEqual(result["operation_count"], 10)
        self.assertEqual(self.store.event_count(), 1)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM artifacts"), 1)
        record = json.loads(self.store.rows("SELECT payload_json FROM artifacts")[0][0])
        self.assertIsNotNone(record["consumed_at"])
        for operation in TARGETS:
            target = self.root / operation["path"]
            expected = operation.get("after_canonical_json_sha256", operation.get("after_bytes_sha256"))
            self.assertEqual(sha256_of_file(target), expected)
        self.assertFalse((self.root / "agent-system/runtime/governance-apply-journal.json").exists())

    def test_replay_is_idempotent_after_canonical_tree_is_after(self) -> None:
        first = self._applier().apply(self.proposal_path, self.approval_path)
        second = self._applier().apply(self.proposal_path, self.approval_path)
        self.assertEqual(first["proposal_hash"], second["proposal_hash"])
        self.assertEqual(self.store.event_count(), 1)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM artifacts"), 1)

    def test_expired_approval_rejects_without_change(self) -> None:
        before = self._before()
        expired = dict(self.approval)
        expired["expires_at"] = "2026-10-10T06:59:59.000000Z"
        # A changed sealed field must be rejected before any filesystem work.
        self.approval_path.write_text(json.dumps(expired), encoding="utf-8")
        with self.assertRaises(Exception):
            self._applier().apply(self.proposal_path, self.approval_path)
        self.assertEqual(before, self._before())
        self.assertEqual(self.store.event_count(), 0)

    def test_failure_after_staging_restores_before_tree_and_approval(self) -> None:
        before = self._before()

        def fail(stage: str, index: int) -> None:
            if stage == "after_staging":
                raise RuntimeError("synthetic staging failure")

        with self.assertRaises(RuntimeError):
            self._applier(failure_injector=fail).apply(self.proposal_path, self.approval_path)
        self.assertEqual(before, self._before())
        self.assertEqual(self.store.event_count(), 0)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM artifacts"), 0)

    def test_failure_mid_rename_restores_before_tree_and_approval(self) -> None:
        before = self._before()

        def fail(stage: str, index: int) -> None:
            if stage == "mid_rename" and index == 0:
                raise RuntimeError("synthetic rename failure")

        with self.assertRaises(RuntimeError):
            self._applier(failure_injector=fail).apply(self.proposal_path, self.approval_path)
        self.assertEqual(before, self._before())
        self.assertEqual(self.store.event_count(), 0)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM artifacts"), 0)

    def test_postcommit_output_failure_is_recoverable_and_idempotent(self) -> None:
        def fail(stage: str, index: int) -> None:
            if stage == "postcommit":
                raise RuntimeError("synthetic output failure")

        with self.assertRaises(RuntimeError):
            self._applier(failure_injector=fail).apply(self.proposal_path, self.approval_path)
        self.assertEqual(self.store.event_count(), 1)
        self.assertTrue((self.root / "agent-system/runtime/governance-apply-journal.json").exists())
        result = self._applier().apply(self.proposal_path, self.approval_path)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.store.event_count(), 1)
        self.assertFalse((self.root / "agent-system/runtime/governance-apply-journal.json").exists())


if __name__ == "__main__":
    unittest.main()
