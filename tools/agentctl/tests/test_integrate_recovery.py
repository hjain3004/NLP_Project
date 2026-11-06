"""Crash reconciliation and concurrency for candidate integration.

Git and SQLite are not one transaction. The crash tests run a real child process
that dies (``os._exit``) at an injected instant, leaving real Git refs and a real
event store exactly as a kill at that instant would. The crash instant is
SIMULATED; everything it leaves behind and everything that repairs it is real.
The concurrency tests use real parallel processes and real threads.
"""

from __future__ import annotations

import json
import subprocess
import threading
import unittest

from agentctl import integrate as integrate_module
from agentctl.integrate import integrate
from agentctl.integration_queue import (
    IntegrationBusy,
    IntegrationRefused,
    claim_integration,
    complete_integration,
    new_lease,
    next_lease_epoch,
    renew_lease,
    store_query,
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


    def test_simulated_branch_advance_after_the_crash_is_still_recognised_by_first_parent_chain(self) -> None:
        """Protects reconciliation: if later commits sit on top of the planned merge, the Git step is
        still recognised as applied (first-parent reachability), not mistaken for unrelated movement."""
        self.assertEqual(self.lab.run_until_killed("T2", "after_ref_update"), 97)
        planned = self.intent("T2")["planned_commit"]
        later = self.lab.git(
            "commit-tree", f"{planned}^{{tree}}", "-p", planned, "-m", "work landed after the crash"
        )
        self.lab.git("update-ref", f"refs/heads/{BRANCH}", later, planned)

        run = self.lab.cli()

        self.assertEqual(run.code, 0, run.stderr)
        self.assertEqual(run.json["outcome"], "reconciled")
        self.assertEqual(self.lab.branch_head(), later)  # untouched
        (record,) = self.lab.artifacts("integration-record", "T2")
        self.assertEqual(record["payload"]["integration_commit"], planned)
        self.assertEqual(self.lab.state("T2"), "completed")

    def test_branch_moved_between_planning_and_claim_leaves_the_task_unclaimed(self) -> None:
        """Protects the claim transaction: a plan computed against a stale head is never claimed."""
        moved: dict[str, str] = {}

        def another_writer(point: str) -> None:
            if point == "after_plan":
                moved["head"] = self.lab.git(
                    "commit-tree", f"{self.head_after_first}^{{tree}}", "-p",
                    self.head_after_first, "-m", "writer between plan and claim",
                )
                self.lab.git("update-ref", f"refs/heads/{BRANCH}", moved["head"], self.head_after_first)

        result = integrate("T2", store=self.lab.store, fault_hook=another_writer)

        self.assertEqual(result["reason_code"], "branch_moved_after_planning")
        self.assertEqual(self.lab.state("T2"), "integration_ready")  # nothing claimed, nothing to reconcile
        self.assertEqual(self.lab.artifacts("integration-intent", "T2"), [])
        self.assertEqual(self.lab.branch_head(), moved["head"])
        self.assertEqual(self.lab.leftover_integration_worktrees(), [])

    def test_replay_that_would_not_reproduce_the_recorded_commit_is_refused(self) -> None:
        """Protects replay: recovery advances the branch only to the exact commit the intent promised."""
        self.assertEqual(self.lab.run_until_killed("T2", "after_intent"), 97)
        # SIMULATED divergence: the recorded plan time no longer reproduces the recorded commit.
        self.lab.tamper(
            "UPDATE artifacts SET payload_json = json_set(payload_json, '$.planned_at', "
            "json_extract(payload_json, '$.planned_at') + 1) "
            "WHERE artifact_type = 'integration-intent' AND task_id = 'T2'"
        )

        run = self.lab.cli("T2")

        self.assertEqual(run.code, 1)
        self.assertEqual(run.json["reason_code"], "reconciliation_required")
        self.assertNotEqual(run.json["details"]["recorded"], run.json["details"]["replayed"])
        self.assertEqual(self.lab.branch_head(), self.head_after_first)
        self.assertEqual(self.lab.state("T2"), "integrating")
        self.assertEqual(self.lab.artifacts("integration-record", "T2"), [])

    def test_expired_integration_lease_is_replaced_with_a_new_epoch_and_token_on_recovery(self) -> None:
        """Protects lease fencing: a lease that expired during the outage cannot complete the integration."""
        self.assertEqual(self.lab.run_until_killed("T2", "after_intent"), 97)
        (old,) = self.lab.artifacts("integration-lease", "T2")
        # SIMULATED passage of time.
        self.lab.tamper(
            "UPDATE artifacts SET payload_json = json_set(payload_json, '$.expires_at', "
            "'2000-01-01T00:00:00.000000Z') WHERE artifact_id = ?",
            (old["artifact_id"],),
        )

        run = self.lab.cli("T2")

        self.assertEqual(run.code, 0, run.stderr)
        (record,) = self.lab.artifacts("integration-record", "T2")
        manifest = record["payload"]
        self.assertEqual(manifest["lease_epoch"], old["payload"]["lease_epoch"] + 1)
        self.assertNotEqual(manifest["lease_id"], old["payload"]["lease_id"])
        self.assertNotEqual(manifest["fencing_token"], old["payload"]["fencing_token"])
        self.assertEqual(len(self.lab.events("integration_lease_renewed", "T2")), 1)
        retired = {a["payload"]["lease_id"]: a["integrable"] for a in self.lab.artifacts("integration-lease", "T2")}
        self.assertEqual(retired[old["payload"]["lease_id"]], 0)

    def _expire(self, lease_artifact_id: str) -> None:
        # SIMULATED passage of time.
        self.lab.tamper(
            "UPDATE artifacts SET payload_json = json_set(payload_json, '$.expires_at', "
            "'2000-01-01T00:00:00.000000Z') WHERE artifact_id = ?",
            (lease_artifact_id,),
        )

    def test_lease_that_expired_after_the_ref_update_is_renewed_before_completion(self) -> None:
        """F3 (the reviewer's poc_lease): killed at after_ref_update, lease expired, the rerun completed
        with the same lease id, token and epoch 1 and no renewal."""
        self.assertEqual(self.lab.run_until_killed("T2", "after_ref_update"), 97)
        (old,) = self.lab.artifacts("integration-lease", "T2")
        self._expire(old["artifact_id"])

        run = self.lab.cli("T2")

        self.assertEqual(run.code, 0, run.stdout + run.stderr)
        self.assertEqual(run.json["reconciled"], ["T2"])
        (record,) = self.lab.artifacts("integration-record", "T2")
        manifest = record["payload"]
        self.assertNotEqual(manifest["lease_id"], old["payload"]["lease_id"])
        self.assertNotEqual(manifest["fencing_token"], old["payload"]["fencing_token"])
        self.assertEqual(manifest["lease_epoch"], old["payload"]["lease_epoch"] + 1)
        self.assertEqual(len(self.lab.events("integration_lease_renewed", "T2")), 1)
        self.assertEqual(self.lab.commit_count(), self.count_after_first + 2)  # no second merge

    def test_completion_is_refused_for_an_expired_lease(self) -> None:
        """F3: transaction 2 itself must refuse a lease that is no longer live."""
        self.assertEqual(self.lab.run_until_killed("T2", "after_ref_update"), 97)
        intent = self.intent("T2")
        (lease,) = self.lab.artifacts("integration-lease", "T2")
        self._expire(lease["artifact_id"])
        (expired,) = self.lab.artifacts("integration-lease", "T2")
        manifest = integrate_module._manifest(intent, expired["payload"])

        with self.assertRaises(IntegrationRefused) as raised:
            complete_integration(self.lab.store, intent=intent, manifest=manifest)

        self.assertEqual(raised.exception.reason_code, "lease_expired")
        self.assertEqual(self.lab.state("T2"), "integrating")
        self.assertEqual(self.lab.artifacts("integration-record", "T2"), [])

    def test_completion_is_refused_for_a_stale_fencing_token_or_epoch(self) -> None:
        """F3: a manifest whose token or epoch differs from the current lease artifact cannot complete."""
        self.assertEqual(self.lab.run_until_killed("T2", "after_ref_update"), 97)
        intent = self.intent("T2")
        (lease,) = self.lab.artifacts("integration-lease", "T2")
        for field, value in (("fencing_token", "0" * 64), ("lease_epoch", 7)):
            manifest = integrate_module._manifest(intent, {**lease["payload"], field: value})
            with self.subTest(field=field):
                with self.assertRaises(IntegrationRefused) as raised:
                    complete_integration(self.lab.store, intent=intent, manifest=manifest)
                self.assertEqual(raised.exception.reason_code, "lease_fencing_mismatch")
        self.assertEqual(self.lab.state("T2"), "integrating")
        self.assertEqual(self.lab.artifacts("integration-record", "T2"), [])

    def test_completion_is_refused_if_the_intent_is_no_longer_integrable(self) -> None:
        """Protects transaction 2: a retired intent or lease cannot be used to complete an integration."""
        self.assertEqual(self.lab.run_until_killed("T2", "after_ref_update"), 97)
        intent = self.intent("T2")
        (lease,) = self.lab.artifacts("integration-lease", "T2")
        manifest = integrate_module._manifest(intent, lease["payload"])
        self.lab.tamper(
            "UPDATE artifacts SET integrable = 0 WHERE artifact_type = 'integration-intent' AND task_id = 'T2'"
        )

        with self.assertRaises(IntegrationRefused) as raised:
            complete_integration(self.lab.store, intent=intent, manifest=manifest)

        self.assertEqual(raised.exception.reason_code, "intent_not_integrable")
        self.assertEqual(self.lab.state("T2"), "integrating")
        self.assertEqual(self.lab.artifacts("integration-record", "T2"), [])


class LeaseHolderFencingTests(RecoveryTestCase):
    """Review B3 #3: `_finalize` compared the store's current lease with a manifest built from that very
    lease (a self-comparison), so a takeover by another holder, or a live lease of another live process,
    was silently adopted. The finalizer must act under the lease THIS process claimed."""

    def _expire(self, lease_artifact_id: str) -> None:
        # SIMULATED passage of time.
        self.lab.tamper(
            "UPDATE artifacts SET payload_json = json_set(payload_json, '$.expires_at', "
            "'2000-01-01T00:00:00.000000Z') WHERE artifact_id = ?",
            (lease_artifact_id,),
        )

    def _usurp(self, task_id: str) -> dict:
        """A different holder installs a newer lease for the task (a real, ordinary renewal call)."""
        intent = self.intent(task_id)
        usurper = new_lease(task_id, intent["integration_id"], next_lease_epoch(store_query(self.lab.store), task_id),
                            "usurping-integrator")
        renew_lease(self.lab.store, intent=intent, lease=usurper)
        return usurper

    def test_the_claiming_process_is_fenced_out_when_another_holder_took_the_lease(self) -> None:
        """The claiming process was killed-and-replaced: after the Git step another holder installs a newer
        lease. Completing with the store's current lease (the old behaviour) adopted the usurper's; the
        claimant must be refused instead, and nothing completes."""
        taken: dict = {}

        def takeover(point: str) -> None:
            if point == "after_ref_update":  # injected: the real renewal by a second holder
                taken["lease"] = self._usurp("T2")

        result = integrate("T2", store=self.lab.store, fault_hook=takeover)

        self.assertEqual(result["outcome"], "refused", result)
        # The usurper's renewal retired our lease, so transaction 2 refuses it as not integrable; a lease
        # that is still integrable but not current is refused as a fencing mismatch. Either way: fenced out.
        self.assertIn(result["reason_code"], {"lease_not_integrable", "lease_fencing_mismatch"}, result)
        self.assertEqual(self.lab.state("T2"), "integrating")
        self.assertEqual(self.lab.artifacts("integration-record", "T2"), [])
        live = [a["payload"]["lease_id"] for a in self.lab.artifacts("integration-lease", "T2") if a["integrable"]]
        self.assertEqual(live, [taken["lease"]["lease_id"]])  # the usurper still holds it, untouched

    def test_reconcile_refuses_a_live_lease_whose_holder_is_another_live_process(self) -> None:
        """SIMULATED holder: the crashed run's lease is rewritten to name a real, running process."""
        self.assertEqual(self.lab.run_until_killed("T2", "after_ref_update"), 97)
        (lease,) = self.lab.artifacts("integration-lease", "T2")
        holder = subprocess.Popen(["sleep", "60"])
        self.addCleanup(lambda: (holder.kill(), holder.wait()))
        ticks = integrate_module._proc_start_ticks(holder.pid)
        self.lab.tamper(
            "UPDATE artifacts SET payload_json = json_set(payload_json, '$.holder', json(?)) WHERE artifact_id = ?",
            (json.dumps({"pid": holder.pid, "start_ticks": ticks, "boot_id": integrate_module._boot_id()}),
             lease["artifact_id"]),
        )

        refused = self.lab.cli()

        self.assertEqual(refused.code, 1, refused.stdout + refused.stderr)
        self.assertEqual(refused.json["reason_code"], "lease_held_by_live_holder")
        self.assertEqual(self.lab.state("T2"), "integrating")
        self.assertEqual(self.lab.artifacts("integration-record", "T2"), [])
        self.assertEqual(self.lab.events("integration_lease_renewed", "T2"), [])
        self.assertEqual(self.findings("T2"), [])  # refused before anything was recorded

        holder.kill()
        holder.wait()  # the holder is gone: now the lease may be taken over
        recovered = self.lab.cli()

        self.assertEqual(recovered.code, 0, recovered.stdout + recovered.stderr)
        self.assertEqual(recovered.json["outcome"], "reconciled")
        (record,) = self.lab.artifacts("integration-record", "T2")
        self.assertEqual(record["payload"]["lease_id"], lease["payload"]["lease_id"])

    def test_reconcile_waits_for_expiry_when_a_live_lease_has_no_identifiable_holder(self) -> None:
        """SIMULATED legacy lease (no holder identity): not adopted while live; renewed once expired."""
        self.assertEqual(self.lab.run_until_killed("T2", "after_ref_update"), 97)
        (lease,) = self.lab.artifacts("integration-lease", "T2")
        self.lab.tamper(
            "UPDATE artifacts SET payload_json = json_remove(payload_json, '$.holder') WHERE artifact_id = ?",
            (lease["artifact_id"],),
        )

        refused = self.lab.cli()

        self.assertEqual(refused.json["reason_code"], "lease_holder_unknown")
        self.assertEqual(self.lab.state("T2"), "integrating")
        self._expire(lease["artifact_id"])
        recovered = self.lab.cli()
        self.assertEqual(recovered.code, 0, recovered.stdout + recovered.stderr)
        (record,) = self.lab.artifacts("integration-record", "T2")
        self.assertEqual(record["payload"]["lease_epoch"], lease["payload"]["lease_epoch"] + 1)

    def test_renewal_verifies_inside_its_transaction_that_the_old_lease_is_expired_and_still_current(self) -> None:
        self.assertEqual(self.lab.run_until_killed("T2", "after_ref_update"), 97)
        intent = self.intent("T2")
        (old,) = self.lab.artifacts("integration-lease", "T2")
        replacement = lambda: new_lease("T2", intent["integration_id"], 2, "renewing-integrator")  # noqa: E731

        with self.assertRaises(IntegrationRefused) as live:
            integrate_module._renew_expired_lease(
                self.lab.store, intent=intent, expired=old["payload"], lease=replacement()
            )
        self.assertEqual(live.exception.reason_code, "lease_not_expired")

        self._expire(old["artifact_id"])
        self._usurp("T2")  # someone else replaced it first
        with self.assertRaises(IntegrationRefused) as stolen:
            integrate_module._renew_expired_lease(
                self.lab.store, intent=intent, expired=old["payload"], lease=replacement()
            )
        self.assertEqual(stolen.exception.reason_code, "lease_fencing_mismatch")
        self.assertEqual(len(self.lab.events("integration_lease_renewed", "T2")), 1)  # only the usurper's


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
