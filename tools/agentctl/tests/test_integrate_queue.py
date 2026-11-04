"""The integration queue is deterministic: stored facts decide the order, never the caller."""

from __future__ import annotations

import unittest

from agentctl.integration_queue import build_queue, store_query

from tests.helpers_lane_b import Lab


class QueueDeterminismTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lab = Lab()
        self.addCleanup(self.lab.close)
        # Created in a deliberately non-sorted order: creation order must not matter.
        for task_id, name in (("T-c", "c"), ("T-a", "a"), ("T-b", "b")):
            self.lab.candidate(task_id, {f"src/{name}.txt": f"{name}\n"})

    def head_and_order(self) -> list[str]:
        queue = build_queue(store_query(self.lab.store), None)
        return [entry.task_id for entry in queue.eligible]

    def test_order_is_topological_then_task_id_regardless_of_creation_order(self) -> None:
        """Protects determinism: the same stored facts always yield the same head."""
        self.assertEqual(self.head_and_order(), ["T-a", "T-b", "T-c"])
        integrated = []
        for _ in range(3):
            run = self.lab.cli()
            self.assertEqual(run.code, 0, run.stderr)
            integrated.append(run.json["task_id"])
        self.assertEqual(integrated, ["T-a", "T-b", "T-c"])

    def test_explicit_request_for_a_non_head_task_is_refused_naming_the_head(self) -> None:
        """Protects against caller preference: only the queue head may be requested by id."""
        run = self.lab.cli("T-b")

        self.assertEqual(run.code, 1)
        self.assertEqual(run.json["reason_code"], "not_queue_head")
        self.assertEqual(run.json["details"]["head"], "T-a")
        self.assertIsNone(self.lab.branch_head())
        self.assertEqual(self.lab.state("T-b"), "integration_ready")
        self.assertEqual(self.lab.events("integration_lease_issued"), [])
        head = self.lab.cli("T-a")  # the head itself is accepted
        self.assertEqual(head.code, 0, head.stderr)

    def test_contract_integration_order_outranks_topological_position(self) -> None:
        """Protects the contract's explicit ordering for independent candidates."""
        # SIMULATED payload: TaskSpec has no integration_order field yet (a discovered dependency),
        # so the contract value is injected into the stored task payload the queue reads.
        self.lab.tamper("UPDATE tasks SET payload_json = json_set(payload_json, '$.integration_order', 1) WHERE task_id = 'T-c'")
        self.lab.tamper("UPDATE tasks SET payload_json = json_set(payload_json, '$.integration_order', 2) WHERE task_id = 'T-b'")

        self.assertEqual(self.head_and_order(), ["T-c", "T-b", "T-a"])  # T-a has no order: last
        run = self.lab.cli()
        self.assertEqual(run.json["task_id"], "T-c")


if __name__ == "__main__":
    unittest.main()
