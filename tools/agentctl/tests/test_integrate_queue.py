"""The integration queue is deterministic: stored facts decide the order, never the caller."""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from agentctl.integration_queue import (
    CANDIDATE_AUTHOR_MARKER,
    CANDIDATE_BINDING_FINDING_ID,
    CHALLENGER_NOT_MERGE_ELIGIBLE,
    build_queue,
    store_query,
)

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


PROMOTION_TEXT = "owner promotion of the binding (registry change under champion-challenger-v1) is required"


class ChallengerMergeEligibilityTests(unittest.TestCase):
    """champion-challenger-v1 `challenger_merge_eligible: false`, enforced from STORED evidence.

    Real Git, real store, real ``agentctl integrate`` processes. The candidate disclosure the control plane
    writes during ``run`` / ``review run`` (see ``test_execute.CandidateBindingRunTests`` for the real
    producer) is applied to Lab tasks with ``Lab.tamper``: SIMULATED fixture state, labelled as such.
    """

    def setUp(self) -> None:
        self.lab = Lab()
        self.addCleanup(self.lab.close)
        # T-a sorts first, so a queue that did not skip it would pick it as head.
        self.lab.candidate("T-a", {"src/a.txt": "a\n"})
        self.lab.candidate("T-b", {"src/b.txt": "b\n"})

    # -- SIMULATED stored evidence ------------------------------------------------------------

    def _payload(self, sql: str, params: tuple) -> dict:
        return json.loads(self.lab.store.scalar(sql, params))

    def mark(self, task_id: str, kind: str) -> None:
        if kind == "reviewer_finding":
            payload = self._payload("SELECT payload_json FROM reviews WHERE task_id = ?", (task_id,))
            payload["findings"].append({
                "finding_id": CANDIDATE_BINDING_FINDING_ID, "severity": "important", "blocking": False,
                "observed_behavior": "[control-plane note, not a reviewer finding] unevaluated candidate",
            })
            self.lab.tamper("UPDATE reviews SET payload_json = ? WHERE task_id = ?", (json.dumps(payload), task_id))
        elif kind == "author_marker":
            row = "SELECT payload_json FROM artifacts WHERE task_id = ? AND artifact_type = 'implementation-manifest'"
            payload = self._payload(row, (task_id,))
            payload["deviations"] = [f"{CANDIDATE_AUTHOR_MARKER} The worker model binding is an unevaluated candidate."]
            self.lab.tamper(
                "UPDATE artifacts SET payload_json = ? WHERE task_id = ? AND artifact_type = 'implementation-manifest'",
                (json.dumps(payload), task_id),
            )
        elif kind in ("reviewer_run", "author_run"):
            role = kind.split("_")[0]
            self.add_model_run(task_id, f"{role}-run-{task_id}", role, "candidate")
        else:
            raise AssertionError(kind)

    def add_model_run(self, task_id: str, run_id: str, role: str, binding_type: str, attempt: str | None = None) -> None:
        attempt = attempt or f"{task_id}-attempt-1"
        self.lab.tamper(
            "INSERT INTO model_runs(run_id, task_id, attempt_id, provider, model_version, status, usage_json, payload_json) "
            "VALUES (?, ?, ?, 'anthropic', 'm', 'completed', '{}', ?)",
            (run_id, task_id, attempt, json.dumps({"kind": role, "binding_type": binding_type})),
        )

    KINDS = ("reviewer_finding", "reviewer_run", "author_marker", "author_run")

    # -- the queue --------------------------------------------------------------------------

    def test_a_task_approved_or_authored_through_a_candidate_is_parked_and_does_not_block_the_queue(self) -> None:
        """Protects champion-challenger-v1: stored candidate evidence makes the task ineligible (reviewer or author, from the verdict, the manifest or the model run); the champion task behind it is still the head."""
        for kind in self.KINDS:
            with self.subTest(evidence=kind):
                self.setUp()  # fresh Lab per evidence kind
                self.mark("T-a", kind)
                queue = build_queue(store_query(self.lab.store), None)
                entry = queue.entry("T-a")
                self.assertEqual(entry.parked_reason, CHALLENGER_NOT_MERGE_ELIGIBLE)
                self.assertFalse(entry.eligible)
                self.assertEqual([e.task_id for e in queue.eligible], ["T-b"])
                self.assertEqual(queue.head.task_id, "T-b")
                self.assertIsNone(queue.entry("T-b").parked_reason)
                self.assertEqual(self.lab.state("T-a"), "integration_ready")  # the review passed; only merging is withheld

    def test_unmarked_and_champion_marked_evidence_does_not_park(self) -> None:
        """Protects against over-blocking: explicit champion model runs and unmarked tasks stay eligible."""
        self.add_model_run("T-b", "reviewer-run-T-b", "reviewer", "initial_prior")
        self.add_model_run("T-b", "author-run-T-b", "author", "initial_prior")
        queue = build_queue(store_query(self.lab.store), None)
        self.assertEqual([e.task_id for e in queue.eligible], ["T-a", "T-b"])

    def test_candidate_evidence_about_another_attempt_or_run_does_not_park(self) -> None:
        """Protects: only the evidence behind the CURRENT attempt's approval counts (a repaired task is judged on its new attempt)."""
        self.add_model_run("T-b", "reviewer-run-OTHER", "reviewer", "candidate")  # not the approving reviewer's run
        self.add_model_run("T-b", "author-run-T-b-old", "author", "candidate", attempt="T-b-attempt-0")
        payload = self._payload("SELECT payload_json FROM reviews WHERE task_id = 'T-b'", ())
        payload["findings"].append({"finding_id": CANDIDATE_BINDING_FINDING_ID, "blocking": False})
        self.lab.tamper(  # a candidate review of an earlier attempt of the same task
            "INSERT INTO reviews(review_id, task_id, attempt_id, verdict, reviewer_session_id, reviewer_run_id, payload_json) "
            "VALUES ('T-b-old-review', 'T-b', 'T-b-attempt-0', 'no_blocking_findings', 's', 'r', ?)", (json.dumps(payload),))
        self.assertIsNone(build_queue(store_query(self.lab.store), None).entry("T-b").parked_reason)

    def test_a_later_registry_edit_cannot_re_qualify_an_old_review(self) -> None:
        """Protects: candidate-ness is read from stored evidence; 'promoting' the binding in the registry file changes nothing for an already-approved task."""
        self.mark("T-a", "reviewer_finding")
        registry = self.lab.root / "agent-system/routing/capability-registry.json"
        document = json.loads(registry.read_text())
        document["entries"] = [e for e in document["entries"] if e["binding_type"] != "candidate"]
        for entry in document["entries"]:
            if entry["capability_class"] == "complex_implementation":
                entry.update(provider="anthropic", model_id="claude-opus-5-5")
        registry.write_text(json.dumps(document))
        self.assertEqual(build_queue(store_query(self.lab.store), None).entry("T-a").parked_reason,
                         CHALLENGER_NOT_MERGE_ELIGIBLE)

    # -- the integrate command ------------------------------------------------------------------

    def test_integrate_skips_the_parked_task_integrates_the_champion_and_never_re_rejects(self) -> None:
        """Protects: no head move writes a rejection for the parked task, and the queue reports why it is waiting."""
        self.mark("T-a", "reviewer_finding")
        first = self.lab.cli()
        self.assertEqual((first.code, first.json["task_id"]), (0, "T-b"))
        head = self.lab.branch_head()
        events = self.lab.store.event_count()

        for _ in range(2):
            idle = self.lab.cli()
            self.assertEqual((idle.code, idle.json["outcome"]), (3, "queue_empty"))
            self.assertEqual(idle.json["parked_without_new_evidence"], {"T-a": CHALLENGER_NOT_MERGE_ELIGIBLE})
        self.assertEqual(self.lab.branch_head(), head)
        self.assertEqual(self.lab.store.event_count(), events, "a parked task must cost no event per call")
        self.assertEqual(self.lab.artifacts("integration-rejection", "T-a"), [])
        self.assertEqual(self.lab.events("integration_lease_issued", "T-a"), [])
        self.assertEqual(self.lab.state("T-a"), "integration_ready")

    def test_explicit_request_for_the_parked_task_is_refused_naming_the_reason_and_the_remedy(self) -> None:
        """Protects: `integrate T-a` says challenger_not_merge_eligible and that owner promotion is required; Git and the store are untouched."""
        for kind in self.KINDS:
            with self.subTest(evidence=kind):
                self.setUp()
                self.mark("T-a", kind)
                events = self.lab.store.event_count()
                refused = self.lab.cli("T-a")
                self.assertEqual(refused.code, 1)
                self.assertEqual(refused.json["outcome"], "refused")
                self.assertEqual(refused.json["reason_code"], CHALLENGER_NOT_MERGE_ELIGIBLE)
                self.assertIn(PROMOTION_TEXT, refused.json["message"])
                self.assertIn("champion-challenger-v1", refused.json["message"])
                self.assertIsNone(self.lab.branch_head())
                self.assertEqual(self.lab.store.event_count(), events)
                self.assertEqual(self.lab.artifacts("integration-rejection"), [])
                self.assertEqual(self.lab.events("integration_lease_issued"), [])
                self.assertEqual(self.lab.state("T-a"), "integration_ready")
                self.assertEqual(self.lab.cli("T-b").code, 0)  # the champion task is unaffected

    def test_no_environment_variable_or_flag_lifts_the_restriction(self) -> None:
        """Protects: the routing opt-in (flag / AGENTCTL_ALLOW_CANDIDATE_BINDINGS) is not a merge waiver; integrate has no waiver at all."""
        self.mark("T-a", "reviewer_finding")
        with mock.patch.dict(os.environ, {"AGENTCTL_ALLOW_CANDIDATE_BINDINGS": "1"}):
            refused = self.lab.cli("T-a")
            queue_run = self.lab.cli()
        self.assertEqual((refused.code, refused.json["reason_code"]), (1, CHALLENGER_NOT_MERGE_ELIGIBLE))
        self.assertEqual((queue_run.code, queue_run.json["task_id"]), (0, "T-b"))  # not T-a
        flagged = self.lab.cli("T-a", "--allow-candidate-bindings")
        self.assertEqual(flagged.code, 2)  # unknown option: usage error, nothing runs
        self.assertEqual(self.lab.state("T-a"), "integration_ready")
        self.assertEqual(self.lab.artifacts("integration-record", "T-a"), [])


if __name__ == "__main__":
    unittest.main()
