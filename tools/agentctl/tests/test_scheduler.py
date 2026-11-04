"""State-machine and ten-condition scheduler admission tests."""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from agentctl import scheduler as scheduler_module
from agentctl.errors import IllegalTransition
from agentctl.models import TaskState
from agentctl.execute import run_task
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


class VerifyCandidateHardeningTests(unittest.TestCase):
    """Deterministic checks run with a timeout and without credentials in their environment.

    The candidate comes from a real ``run`` against a SIMULATED provider (fake ``claude``).
    """

    def _candidate(self, checks: tuple[str, ...]):
        from .helpers_lane_a import LaneARepo, install_fake_claude, proposal

        self.bin = tempfile.TemporaryDirectory()
        self.addCleanup(self.bin.cleanup)
        install_fake_claude(pathlib.Path(self.bin.name), structured_output=proposal())
        patcher = mock.patch.dict(
            os.environ,
            {"PATH": f"{self.bin.name}{os.pathsep}{os.environ['PATH']}",
             "ANTHROPIC_API_KEY": "sk-ant-sentinel-not-real", "AGENTCTL_TEST_SECRET": "sentinel"},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        repo = LaneARepo(
            self, deterministic_checks=checks,
            registry={"standard_implementation": ("anthropic", "claude-sonnet-5"),
                      "complex_implementation": ("anthropic", "claude-sonnet-5")},
        )
        run_task(repo.TASK_ID, store=repo.store, timeout_seconds=60, max_budget_usd=0.5)
        return repo

    def test_a_hanging_check_is_a_failing_check_and_its_process_group_is_killed(self) -> None:
        from .helpers_lane_a import pid_alive

        pid_file = pathlib.Path(tempfile.mkdtemp()) / "check.pid"
        repo = self._candidate((f"sleep 300 & echo $! > {pid_file}; wait",))
        with mock.patch.object(scheduler_module, "CHECK_TIMEOUT_SECONDS", 1.5):
            result = repo.scheduler.verify_candidate(repo.TASK_ID)
        self.assertEqual(result["state"], "repair_required")
        evidence = json.loads(repo.store.rows(
            "SELECT payload_json FROM artifacts WHERE artifact_type = 'verification-evidence'"
        )[0]["payload_json"])
        self.assertFalse(evidence["passed"])
        self.assertEqual((evidence["checks"][0]["exit_code"], evidence["checks"][0]["timed_out"]), (124, True))
        self.assertFalse(pid_alive(int(pid_file.read_text())), "check descendant survived")

    def test_checks_do_not_see_provider_credentials_or_the_operator_home(self) -> None:
        real_home = os.path.expanduser("~")
        script = (
            "if env | grep -E '^(CLAUDE|ANTHROPIC|AGENTCTL_TEST)'; then exit 1; fi; "
            f'[ "$HOME" != "{real_home}" ] && [ -z "$(ls -A "$HOME")" ]'
        )
        repo = self._candidate((script,))
        result = repo.scheduler.verify_candidate(repo.TASK_ID)
        self.assertEqual(result["state"], "independent_review")


class IntegrationOrderTests(unittest.TestCase):
    def test_registered_task_payload_carries_the_contracts_integration_order(self) -> None:
        """Protects: the integration queue can order by dependencies.integration_order, which TaskSpec used to drop."""
        from agentctl.scheduler import TaskSpec
        from .helpers_lane_a import LaneARepo

        repo = LaneARepo(self, integration_order=7)
        payload = json.loads(repo.store.rows("SELECT payload_json FROM tasks")[0]["payload_json"])
        self.assertEqual(payload["integration_order"], 7)
        self.assertEqual(repo.scheduler.task_spec(repo.TASK_ID).integration_order, 7)
        # The ordering field is not part of either hash.
        self.assertEqual(repo.store.rows("SELECT contract_hash FROM tasks")[0]["contract_hash"],
                         repo.contract["identity"]["contract_hash"])
        # Payloads registered before the field existed still load, with the order unknown.
        legacy = {k: v for k, v in payload.items() if k != "integration_order"}
        self.assertIsNone(TaskSpec.from_payload(legacy).integration_order)


if __name__ == "__main__":
    unittest.main()
