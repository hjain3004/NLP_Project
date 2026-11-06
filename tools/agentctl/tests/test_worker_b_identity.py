"""Focused regression coverage for same-class author/reviewer identities."""

from __future__ import annotations

import json
import unittest

from agentctl.execute import review_task

from .test_execute import RunTestCase


class SameCapabilityClassReviewTests(RunTestCase):
    def review(self, repo):
        self.fake.set(structured_output={"verdict": "no_blocking_findings", "findings": []})
        return review_task(repo.TASK_ID, store=repo.store, timeout_seconds=60, max_budget_usd=0.5)

    def test_same_capability_class_uses_distinct_operational_roles_and_can_review(self) -> None:
        """Protects: routing class equality is not mistaken for self-review."""
        repo = self.repo(capability_class="complex_implementation")
        author = self.run_it(repo)
        repo.scheduler.verify_candidate(repo.TASK_ID)
        result = self.review(repo)

        self.assertEqual(result["state"], "integration_ready")
        assignment = json.loads(repo.store.rows(
            "SELECT payload_json FROM artifacts WHERE artifact_type = 'review-assignment'"
        )[0]["payload_json"])
        attempt = repo.store.rows("SELECT payload_json FROM attempts")[0]
        author_payload = json.loads(attempt["payload_json"])

        self.assertEqual(author_payload["author_actor_role"], "complex_implementation")
        self.assertEqual(author_payload["author_operational_role"], "implementation")
        self.assertEqual(assignment["reviewer_role"], "complex_implementation")
        self.assertEqual(assignment["reviewer_capability_class"], "complex_implementation")
        self.assertEqual(assignment["reviewer_operational_role"], "verifier")
        self.assertNotEqual(author["session_id"], result["reviewer_session_id"])
        self.assertNotEqual(author["provider_run_id"], result["reviewer_run_id"])


if __name__ == "__main__":
    unittest.main()
