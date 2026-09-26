"""State-machine and ten-condition scheduler admission tests."""

from __future__ import annotations

import unittest

from agentctl.errors import IllegalTransition
from agentctl.models import TaskState
from agentctl.scheduler import (
    ADMISSION_CONDITIONS,
    SCHEDULER_CYCLE,
    TRANSITION_RULES,
    AdmissionFacts,
    admission_decision,
    transition,
)


class TransitionTableTests(unittest.TestCase):
    def test_transition_rules_match_the_twenty_four_documented_rows(self) -> None:
        self.assertEqual(len(TRANSITION_RULES), 24)

    def test_normal_path_transitions(self) -> None:
        cases = (
            (TaskState.DRAFT, "schema_policy_passed", TaskState.VALIDATED),
            (TaskState.VALIDATED, "admission_passed", TaskState.READY),
            (TaskState.READY, "lease_issued", TaskState.LEASED),
            (TaskState.LEASED, "worker_acknowledged", TaskState.RUNNING),
            (TaskState.RUNNING, "candidate_ingested", TaskState.CANDIDATE),
            (
                TaskState.CANDIDATE,
                "verification_started",
                TaskState.DETERMINISTIC_VERIFICATION,
            ),
            (
                TaskState.DETERMINISTIC_VERIFICATION,
                "checks_passed",
                TaskState.INDEPENDENT_REVIEW,
            ),
            (
                TaskState.INDEPENDENT_REVIEW,
                "review_passed",
                TaskState.INTEGRATION_READY,
            ),
            (
                TaskState.INTEGRATION_READY,
                "integration_lease_issued",
                TaskState.INTEGRATING,
            ),
            (TaskState.INTEGRATING, "integration_passed", TaskState.COMPLETED),
        )
        for state, event, expected in cases:
            with self.subTest(state=state, event=event):
                self.assertEqual(transition(state, event), expected)

    def test_absent_state_event_pair_raises(self) -> None:
        with self.assertRaises(IllegalTransition):
            transition(TaskState.DRAFT, "candidate_ingested")
        with self.assertRaises(IllegalTransition):
            transition(TaskState.COMPLETED, "dependency_changed")

    def test_wildcard_nonterminal_transition_never_changes_terminal_state(self) -> None:
        self.assertEqual(
            transition(TaskState.RUNNING, "dependency_changed"), TaskState.STALE
        )
        for state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED):
            with self.subTest(state=state), self.assertRaises(IllegalTransition):
                transition(state, "dependency_changed")

    def test_multi_outcome_authority_rows_require_an_allowed_decision(self) -> None:
        self.assertEqual(
            transition(TaskState.ESCALATED, "authority_decision", TaskState.BLOCKED),
            TaskState.BLOCKED,
        )
        with self.assertRaises(IllegalTransition):
            transition(TaskState.ESCALATED, "authority_decision", TaskState.READY)


class AdmissionTests(unittest.TestCase):
    def test_all_ten_admission_conditions_are_named_in_order(self) -> None:
        self.assertEqual(len(ADMISSION_CONDITIONS), 10)
        self.assertEqual(
            SCHEDULER_CYCLE,
            (
                "refresh",
                "invalidate_stale_contexts",
                "expire_dead_leases",
                "compute_ready",
                "reject_lock_conflicts",
                "reserve_review_and_integration_capacity",
                "enforce_budgets",
                "issue_leases",
                "checkpoint",
            ),
        )

    def test_each_failed_condition_has_its_own_reason_code(self) -> None:
        expected = (
            "dependency_incomplete",
            "context_stale",
            "base_commit_incompatible",
            "path_lock_conflict",
            "semantic_lock_conflict",
            "capability_floor",
            "reviewer_unavailable",
            "budget_exhausted",
            "enforcement_conflict",
            "human_approval_required",
        )
        for index, reason in enumerate(expected):
            values = [True] * 10
            values[index] = False
            facts = AdmissionFacts(*values)
            decision = admission_decision(facts)
            with self.subTest(reason=reason):
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason_code, reason)

    def test_all_conditions_true_is_admitted(self) -> None:
        decision = admission_decision(AdmissionFacts(*([True] * 10)))
        self.assertTrue(decision.allowed)
        self.assertIsNone(decision.reason_code)


if __name__ == "__main__":
    unittest.main()
