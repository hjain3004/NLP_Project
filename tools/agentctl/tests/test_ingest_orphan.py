"""A late manifest from a superseded attempt is orphan evidence; it must not disturb the newer attempt.

Reproduced by Lane B: attempt 1's manifest arrived after its lease expired while attempt 2 was running with
a live lease. The fencing mismatch REVOKED the new lease and sent the task to repair_required with
orphan_evidence_count 0. Design 14.1: an expired-lease submission becomes orphan evidence and advances
nothing. A manifest that claims the CURRENT lease with a wrong fencing token remains a permission violation.
Real event store; no mocks.
"""

from __future__ import annotations

import unittest

from agentctl.errors import ContractStale, PermissionViolation

from tests.test_leases import LeaseTests as _Fixtures


class LateManifestTests(unittest.TestCase):
    # Reuse the lease fixtures without inheriting (and re-running) that class's tests.
    root = _Fixtures.root
    setUp = _Fixtures.setUp
    tearDown = _Fixtures.tearDown
    _spec = _Fixtures._spec
    _register = _Fixtures._register
    _manifest = _Fixtures._manifest

    def run_two_attempts(self):
        spec = self._register()
        first = self.scheduler.dispatch("TASK-1", worker_identity="worker-1")
        self.scheduler.acknowledge("TASK-1", spec.context_hash)
        self.scheduler.expire_lease("TASK-1", force=True)
        second = self.scheduler.dispatch("TASK-1", worker_identity="worker-2")
        self.scheduler.acknowledge("TASK-1", spec.context_hash)
        self.assertEqual(self.store.status("TASK-1")["state"], "running")
        return spec, first, second

    def active(self, lease_id: str) -> int:
        return self.store.scalar("SELECT active FROM leases WHERE lease_id = ?", (lease_id,))

    def test_a_late_manifest_from_an_expired_attempt_leaves_the_newer_running_attempt_alone(self) -> None:
        spec, first, second = self.run_two_attempts()

        with self.assertRaises(ContractStale):
            self.scheduler.ingest_manifest("TASK-1", self._manifest(first, spec))

        status = self.store.status("TASK-1")
        self.assertEqual(status["state"], "running")
        self.assertEqual(status["orphan_evidence_count"], 1)
        self.assertEqual(self.active(second.lease_id), 1)  # the new lease was not revoked
        rows = self.store.rows(
            "SELECT attempt_id, integrable FROM artifacts WHERE artifact_type = 'implementation-manifest'"
        )
        self.assertEqual([(r["attempt_id"], r["integrable"]) for r in rows], [(first.attempt_id, 0)])
        self.assertEqual(len(self.store.rows("SELECT 1 FROM events WHERE event_type = 'late_artifact'")), 1)
        self.assertEqual(self.store.rows("SELECT 1 FROM events WHERE event_type = 'lease_revoked'"), [])

    def test_the_newer_attempt_can_still_deliver_its_own_manifest_afterwards(self) -> None:
        spec, first, second = self.run_two_attempts()
        with self.assertRaises(ContractStale):
            self.scheduler.ingest_manifest("TASK-1", self._manifest(first, spec))
        manifest = self._manifest(second, spec)
        # The fixture commit is not a real one; reaching the Git binding step proves the lease was honoured.
        with self.assertRaises(PermissionViolation) as raised:
            self.scheduler.ingest_manifest("TASK-1", manifest)
        self.assertEqual(raised.exception.context["mismatch"], "provenance")

    def test_a_manifest_naming_only_an_earlier_epoch_is_orphan_evidence(self) -> None:
        spec, first, second = self.run_two_attempts()
        manifest = self._manifest(second, spec)
        manifest.update(lease_id="unknown-lease", attempt_id="unknown-attempt", lease_epoch=first.epoch)

        with self.assertRaises(ContractStale):
            self.scheduler.ingest_manifest("TASK-1", manifest)

        self.assertEqual(self.store.status("TASK-1")["orphan_evidence_count"], 1)
        self.assertEqual(self.active(second.lease_id), 1)

    def test_a_wrong_fencing_token_on_the_current_lease_still_revokes_it(self) -> None:
        spec, first, second = self.run_two_attempts()
        manifest = self._manifest(second, spec)
        manifest["fencing_token"] = "wrong"

        with self.assertRaises(PermissionViolation):
            self.scheduler.ingest_manifest("TASK-1", manifest)

        self.assertEqual(self.active(second.lease_id), 0)
        self.assertEqual(self.store.status("TASK-1")["state"], "repair_required")
        self.assertEqual(self.store.status("TASK-1")["orphan_evidence_count"], 0)

    def test_a_manifest_naming_the_current_attempt_with_a_foreign_lease_id_still_revokes(self) -> None:
        spec, first, second = self.run_two_attempts()
        manifest = self._manifest(second, spec)
        manifest["lease_id"] = first.lease_id  # claims the current attempt: not an orphan

        with self.assertRaises(PermissionViolation):
            self.scheduler.ingest_manifest("TASK-1", manifest)

        self.assertEqual(self.active(second.lease_id), 0)


del _Fixtures  # keep unittest from collecting the imported class a second time

if __name__ == "__main__":
    unittest.main()
