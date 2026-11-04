"""Crash reconciliation and concurrency for candidate integration.

Git and SQLite are not one transaction. The crash tests run a real child process
that dies (``os._exit``) at an injected instant, leaving real Git refs and a real
event store exactly as a kill at that instant would. The crash instant is
SIMULATED; everything it leaves behind and everything that repairs it is real.
The concurrency tests use real parallel processes and real threads.
"""

from __future__ import annotations

import json
import threading
import unittest

from agentctl.integrate import integrate
from agentctl.integration_queue import (
    IntegrationBusy,
    IntegrationRefused,
    claim_integration,
    new_lease,
)

from tests.helpers_lane_b import BRANCH, Lab


class RecoveryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.lab = Lab()
        self.addCleanup(self.lab.close)
        self.first = self.lab.candidate("T1", {"src/alpha.txt": "alpha\n"})
        self.second = self.lab.candidate("T2", {"src/beta.txt": "beta\n"})
        self.assertEqual(self.lab.cli("T1").code, 0)
        self.head_after_first = self.lab.branch_head()
        self.count_after_first = self.lab.commit_count()

    def intent(self, task_id: str) -> dict:
        (row,) = self.lab.artifacts("integration-intent", task_id)
        return row["payload"]

    def findings(self, task_id: str) -> list[str]:
        return [r["payload"]["finding"] for r in self.lab.artifacts("integration-reconciliation", task_id)]


class CrashReconciliationTests(RecoveryTestCase):
    def test_simulated_crash_after_ref_update_completes_event_store_without_a_second_merge(self) -> None:
        """Protects crash safety: Git advanced but SQLite did not; recovery must not merge again."""
        self.assertEqual(self.lab.run_until_killed("T2", "after_ref_update"), 97)
        intent = self.intent("T2")
        self.assertEqual(self.lab.state("T2"), "integrating")
        self.assertEqual(self.lab.artifacts("integration-record", "T2"), [])
        self.assertEqual(self.lab.branch_head(), intent["planned_commit"])  # Git step really happened
        self.assertEqual(self.lab.commit_count(), self.count_after_first + 2)
        moved_head = self.lab.branch_head()

        recovery = self.lab.cli()

        self.assertEqual(recovery.code, 0, recovery.stderr)
        self.assertEqual(recovery.json["outcome"], "reconciled")
        self.assertEqual(self.lab.state("T2"), "completed")
        self.assertEqual(self.lab.branch_head(), moved_head)  # no second merge commit
        self.assertEqual(self.lab.commit_count(), self.count_after_first + 2)
        (record,) = self.lab.artifacts("integration-record", "T2")
        self.assertEqual(record["payload"]["integration_commit"], moved_head)
        self.assertEqual(self.findings("T2"), ["git_step_applied"])
        self.assertEqual(len(self.lab.events("integration_passed", "T2")), 1)

    def test_simulated_crash_after_intent_before_ref_update_retries_under_the_same_intent(self) -> None:
        """Protects crash safety: intent recorded, Git never moved; recovery redoes the Git step exactly once."""
        self.assertEqual(self.lab.run_until_killed("T2", "after_intent"), 97)
        intent = self.intent("T2")
        self.assertEqual(self.lab.state("T2"), "integrating")
        self.assertEqual(self.lab.branch_head(), self.head_after_first)  # the Git step never happened

        recovery = self.lab.cli("T2")

        self.assertEqual(recovery.code, 0, recovery.stderr)
        self.assertEqual(self.lab.state("T2"), "completed")
        # The pinned plan is reproducible: the resulting commit is the one the intent promised.
        self.assertEqual(self.lab.branch_head(), intent["planned_commit"])
        self.assertEqual(self.lab.commit_count(), self.count_after_first + 2)
        parents = self.lab.git("rev-list", "--parents", "-n1", self.lab.branch_head()).split()[1:]
        self.assertEqual(parents, [self.head_after_first, self.second.result_commit])
        self.assertEqual(self.findings("T2"), ["git_step_not_applied"])
        (record,) = self.lab.artifacts("integration-record", "T2")
        self.assertEqual(record["payload"]["integration_commit"], intent["planned_commit"])
        self.assertEqual(record["payload"]["lease_id"], intent["lease_id"])  # same lease, same intent
        self.assertEqual(len(self.lab.artifacts("integration-intent", "T2")), 1)

    def test_simulated_crash_after_planning_leaves_nothing_claimed_and_the_leftover_worktree_is_cleaned(self) -> None:
        """Protects hygiene: a crash mid-plan leaves no claim; the next run removes the stray worktree."""
        self.assertEqual(self.lab.run_until_killed("T2", "after_plan"), 97)
        self.assertEqual(self.lab.state("T2"), "integration_ready")
        self.assertNotEqual(self.lab.leftover_integration_worktrees(), [])
        self.assertEqual(self.lab.artifacts("integration-intent", "T2"), [])
        self.assertEqual(self.lab.branch_head(), self.head_after_first)

        run = self.lab.cli("T2")

        self.assertEqual(run.code, 0, run.stderr)
        self.assertEqual(self.lab.leftover_integration_worktrees(), [])
        self.assertEqual(self.lab.commit_count(), self.count_after_first + 2)

    def test_simulated_unrelated_branch_movement_is_refused_and_left_for_a_human(self) -> None:
        """Protects the shared branch: if it moved to something unrelated, recovery refuses to guess."""
        self.assertEqual(self.lab.run_until_killed("T2", "after_intent"), 97)
        unrelated = self.lab.git(
            "commit-tree", f"{self.head_after_first}^{{tree}}", "-p", self.head_after_first,
            "-m", "unrelated work on the integration branch",
        )
        self.lab.git("update-ref", f"refs/heads/{BRANCH}", unrelated, self.head_after_first)

        run = self.lab.cli("T2")

        self.assertEqual(run.code, 1)
        self.assertEqual(run.json["reason_code"], "reconciliation_required")
        self.assertEqual(self.lab.state("T2"), "integrating")  # fail closed; no completion invented
        self.assertEqual(self.lab.branch_head(), unrelated)
        self.assertEqual(self.lab.artifacts("integration-record", "T2"), [])
        self.assertEqual(self.findings("T2"), ["branch_moved_unrelated"])
        third = self.lab.candidate("T3", {"src/gamma.txt": "gamma\n"})
        blocked = self.lab.cli("T3")  # the single integration slot stays held
        self.assertEqual(blocked.json["reason_code"], "reconciliation_required")
        self.assertEqual(self.lab.state(third.task_id), "integration_ready")

    def test_compare_and_swap_never_overwrites_a_branch_another_actor_moved_after_the_claim(self) -> None:
        """Protects the shared branch: the ref update is a compare-and-swap, not a blind write."""
        moved: dict[str, str] = {}

        def another_actor_commits(point: str) -> None:
            if point == "after_intent":  # injected: a real concurrent writer to the real ref
                moved["head"] = self.lab.git(
                    "commit-tree", f"{self.head_after_first}^{{tree}}", "-p",
                    self.head_after_first, "-m", "concurrent writer",
                )
                self.lab.git("update-ref", f"refs/heads/{BRANCH}", moved["head"], self.head_after_first)

        result = integrate("T2", store=self.lab.store, fault_hook=another_actor_commits)

        self.assertEqual(result["outcome"], "refused")
        self.assertEqual(result["reason_code"], "branch_moved_during_integration")
        self.assertEqual(self.lab.branch_head(), moved["head"])  # the other writer's commit survives
        self.assertEqual(self.lab.artifacts("integration-record", "T2"), [])
        self.assertEqual(self.lab.state("T2"), "integrating")  # held for reconciliation, not completed


class SerializationTests(RecoveryTestCase):
    def _handmade_intent(self, candidate, integration_id: str) -> tuple[dict, dict]:
        intent = {
            "task_id": candidate.task_id, "attempt_id": candidate.attempt_id,
            "integration_id": integration_id, "branch": BRANCH,
            "branch_head_before": self.head_after_first, "expected_old_head": self.head_after_first,
            "candidate_commit": candidate.result_commit, "planned_commit": candidate.result_commit,
        }
        return intent, new_lease(candidate.task_id, integration_id, 1, "handmade-integrator")

    def test_claim_transaction_refuses_a_second_integration_while_one_is_integrating(self) -> None:
        """Protects serialization inside the transaction (not a process lock): one integrating task at most."""
        third = self.lab.candidate("T3", {"src/gamma.txt": "gamma\n"})
        self.assertEqual(self.lab.run_until_killed("T2", "after_intent"), 97)  # T2 holds the slot
        intent, lease = self._handmade_intent(third, "T3-I1")

        with self.assertRaises(IntegrationBusy) as raised:
            claim_integration(
                self.lab.store, intent=intent, lease=lease,
                branch_head=lambda: self.lab.branch_head(),
            )

        self.assertEqual(raised.exception.reason_code, "integration_in_progress")
        self.assertEqual(self.lab.state("T3"), "integration_ready")
        self.assertEqual(self.lab.artifacts("integration-intent", "T3"), [])
        self.assertEqual(self.lab.artifacts("integration-lease", "T3"), [])

    def test_threads_racing_to_claim_leave_at_most_one_task_integrating(self) -> None:
        """Protects serialization under a real race: concurrent claims never produce two integrating tasks."""
        third = self.lab.candidate("T3", {"src/gamma.txt": "gamma\n"})
        attempts = [(self.second, "T2-I1"), (third, "T3-I1")]
        outcomes: list[str] = []
        barrier = threading.Barrier(len(attempts))

        def claim(candidate, integration_id) -> None:
            intent, lease = self._handmade_intent(candidate, integration_id)
            barrier.wait()
            try:
                claim_integration(
                    self.lab.store, intent=intent, lease=lease,
                    branch_head=lambda: self.lab.branch_head(),
                )
                outcomes.append("claimed")
            except IntegrationRefused as exc:
                outcomes.append(exc.reason_code)

        threads = [threading.Thread(target=claim, args=attempt) for attempt in attempts]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(outcomes.count("claimed"), 1, outcomes)
        integrating = self.lab.store.rows("SELECT task_id FROM tasks WHERE state = 'integrating'")
        self.assertEqual([r["task_id"] for r in integrating], ["T2"])  # the queue head won


class ConcurrentProcessTests(RecoveryTestCase):
    def test_parallel_processes_requesting_the_same_task_produce_exactly_one_integration(self) -> None:
        """Protects against double integration: N real parallel CLI processes, one merge, one record."""
        processes = [self.lab.spawn_cli("T2") for _ in range(4)]
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=120)
            self.assertEqual(process.returncode, 0, stderr)
            results.append(json.loads(stdout.strip().splitlines()[-1]))

        outcomes = sorted(r["outcome"] for r in results)
        self.assertEqual(outcomes, ["already_integrated"] * 3 + ["integrated"])
        self.assertEqual(self.lab.commit_count(), self.count_after_first + 2)
        self.assertEqual(len(self.lab.artifacts("integration-record", "T2")), 1)
        self.assertEqual(len(self.lab.events("integration_passed", "T2")), 1)
        self.assertEqual(len(self.lab.artifacts("integration-intent", "T2")), 1)
        self.assertEqual(self.lab.state("T2"), "completed")

    def test_parallel_processes_without_a_task_id_each_take_a_distinct_queue_head(self) -> None:
        """Protects the queue: concurrent no-argument runs never pick the same candidate twice."""
        self.lab.candidate("T3", {"src/gamma.txt": "gamma\n"})
        processes = [self.lab.spawn_cli() for _ in range(2)]
        taken = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=120)
            self.assertEqual(process.returncode, 0, stderr)
            taken.append(json.loads(stdout.strip().splitlines()[-1])["task_id"])

        self.assertEqual(sorted(taken), ["T2", "T3"])
        self.assertEqual(len(self.lab.events("integration_passed")), 3)  # T1 + the two
        self.assertEqual(self.lab.commit_count(), self.count_after_first + 4)
        for path in ("src/alpha.txt", "src/beta.txt", "src/gamma.txt"):
            self.assertIsNotNone(self.lab.tree_file(path))


if __name__ == "__main__":
    unittest.main()
