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

    def test_a_hanging_check_is_a_failing_check_and_nothing_it_started_survives(self) -> None:
        marker = pathlib.Path(tempfile.mkdtemp()) / "SURVIVOR"
        repo = self._candidate((f"( sleep 5; touch {marker} ) & wait",))
        with mock.patch.object(scheduler_module, "CHECK_TIMEOUT_SECONDS", 1.5):
            result = repo.scheduler.verify_candidate(repo.TASK_ID)
        self.assertEqual(result["state"], "repair_required")
        evidence = json.loads(repo.store.rows(
            "SELECT payload_json FROM artifacts WHERE artifact_type = 'verification-evidence'"
        )[0]["payload_json"])
        self.assertFalse(evidence["passed"])
        self.assertEqual((evidence["checks"][0]["exit_code"], evidence["checks"][0]["timed_out"]), (124, True))
        import time

        time.sleep(7)
        self.assertFalse(marker.exists(), "a process started by the check outlived verification")

    def test_checks_do_not_see_provider_credentials_or_the_operator_home(self) -> None:
        real_home = os.path.expanduser("~")
        script = (
            "if env | grep -E '^(CLAUDE|ANTHROPIC|AGENTCTL_TEST)'; then exit 1; fi; "
            f'[ "$HOME" != "{real_home}" ] && [ -z "$(ls -A "$HOME")" ]'
        )
        repo = self._candidate((script,))
        result = repo.scheduler.verify_candidate(repo.TASK_ID)
        self.assertEqual(result["state"], "independent_review")

    def _evidence(self, repo) -> dict:
        return json.loads(repo.store.rows(
            "SELECT payload_json FROM artifacts WHERE artifact_type = 'verification-evidence'"
        )[0]["payload_json"])

    @mock.patch.dict(os.environ, {"AGENTCTL_CHECK_ISOLATION": "legacy-unsafe"})  # asserts the legacy runner's own defence
    def test_a_setsid_straggler_fails_verification_and_does_not_survive_it(self) -> None:
        import time

        marker = pathlib.Path(tempfile.mkdtemp()) / "STRAGGLER"
        repo = self._candidate((f"setsid sh -c 'sleep 4; touch {marker}' & exit 0",))
        started = time.monotonic()
        result = repo.scheduler.verify_candidate(repo.TASK_ID)
        self.assertLess(time.monotonic() - started, 3.5, "verification waited on an escaped descendant")
        evidence = self._evidence(repo)
        self.assertEqual(result["state"], "repair_required")
        self.assertFalse(evidence["passed"])
        self.assertEqual(evidence["checks"][0]["exit_code"], 0)
        self.assertEqual(evidence["checks"][0]["reason"], "gate_left_processes")
        time.sleep(6)
        self.assertFalse(marker.exists(), "the escaped straggler outlived verification")

    @mock.patch.dict(os.environ, {"AGENTCTL_CHECK_ISOLATION": "legacy-unsafe"})  # asserts the legacy runner's own defence
    def test_a_check_planting_git_config_in_the_shared_repository_fails_verification(self) -> None:
        repo = self._candidate(("git config diff.external /bin/true",))
        result = repo.scheduler.verify_candidate(repo.TASK_ID)
        self.assertEqual(result["state"], "repair_required")
        evidence = self._evidence(repo)
        self.assertFalse(evidence["passed"])
        self.assertEqual(evidence["checks"][0]["exit_code"], 0)  # the check itself "passed"
        self.assertTrue(evidence["repository_tamper"]["changed"])
        reasons = [row["reason_code"] for row in repo.store.rows(
            "SELECT reason_code FROM events WHERE task_id = ? AND event_type = 'implementation_defect'",
            (repo.TASK_ID,))]
        self.assertEqual(reasons, ["gate_tampered_repository"])
        self.assertEqual(repo.store.scalar("SELECT integrable FROM artifacts WHERE artifact_type = 'verification-evidence'"), 0)

    @mock.patch.dict(os.environ, {"AGENTCTL_CHECK_ISOLATION": "legacy-unsafe"})  # asserts the legacy runner's own defence
    def test_the_candidates_own_worktree_bookkeeping_is_not_tampering(self) -> None:
        """A check that merely reads git state (refreshing the worktree's own index) must still pass."""
        repo = self._candidate(("git status --porcelain >/dev/null && git diff --stat HEAD~1 HEAD >/dev/null",))
        result = repo.scheduler.verify_candidate(repo.TASK_ID)
        self.assertEqual(result["state"], "independent_review")
        evidence = self._evidence(repo)
        self.assertTrue(evidence["passed"])
        self.assertNotIn("repository_tamper", evidence)
        self.assertEqual({"command", "exit_code", "stdout_hash", "stderr_hash"} - set(evidence["checks"][0]), set())


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
