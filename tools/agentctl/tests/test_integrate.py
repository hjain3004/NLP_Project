"""Candidate integration against real Git repositories, the real event store and the real CLI.

Every test names the behavior or failure it protects. Where state is corrupted
outside the public API (``Lab.tamper``) the test says SIMULATED: it proves that
integration re-derives evidence rather than trusting stored flags, not that the
corruption can occur through the ordinary commands.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import unittest
from unittest import mock

from agentctl.integrate import IntegrationSettings, integrate
from agentctl.validate import validate_artifact

from tests.helpers_lane_b import BRANCH, INTERFACE_ID, INTERFACE_SOURCE, Lab


class IntegrationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.lab = Lab()
        self.addCleanup(self.lab.close)

    def assert_untouched(self, task_id: str, *, head_before: str | None) -> None:
        """The failure contract: branch unmoved, task not completed, nothing leftover."""
        self.assertEqual(self.lab.branch_head(), head_before)
        self.assertNotEqual(self.lab.state(task_id), "completed")
        self.assertEqual(self.lab.artifacts("integration-record", task_id), [])
        self.assertEqual(self.lab.leftover_integration_worktrees(), [])

    def assert_rejected(self, result: dict, reason: str) -> None:
        self.assertEqual(result["outcome"], "rejected", result)
        self.assertEqual(result["reason_code"], reason, result)


class SuccessfulIntegrationTests(IntegrationTestCase):
    def test_cli_integrates_a_reviewed_candidate_and_state_git_and_record_agree(self) -> None:
        """Protects the delivery cycle: the ordinary command advances Git, state and record together."""
        candidate = self.lab.candidate(
            "T1", {"src/alpha.txt": "alpha\n"}, checks=("grep -q alpha src/alpha.txt",)
        )
        checkout_before = self.lab.git("rev-parse", f"refs/heads/{self.lab.checkout_branch}")
        self.assertIsNone(self.lab.branch_head())

        run = self.lab.cli()  # no task id: the queue chooses

        self.assertEqual(run.code, 0, run.stderr)
        self.assertEqual(run.json["outcome"], "integrated")
        self.assertEqual(run.json["task_id"], "T1")
        # Git: the integration branch carries the unchanged candidate commit and its content.
        self.assertEqual(self.lab.branch_head(), candidate.result_commit)
        self.assertEqual(self.lab.tree_file("src/alpha.txt"), "alpha\n")
        # The user's own checkout branch was never moved.
        self.assertEqual(
            self.lab.git("rev-parse", f"refs/heads/{self.lab.checkout_branch}"), checkout_before
        )
        # Event store: completed, with lease -> passed in order.
        self.assertEqual(self.lab.state("T1"), "completed")
        types = [row["event_type"] for row in self.lab.events(task_id="T1")]
        self.assertLess(types.index("integration_lease_issued"), types.index("integration_passed"))
        # Record: exactly one, schema-valid, and equal to Git facts.
        (record,) = self.lab.artifacts("integration-record", "T1")
        manifest = record["payload"]
        path = (
            self.lab.root / "agent-system/runtime/integration/T1/records"
            / manifest["integration_id"] / "integration-manifest.json"
        )
        self.assertEqual(validate_artifact(path, repository_root=self.lab.root), manifest)
        self.assertEqual(manifest["integration_commit"], self.lab.branch_head())
        self.assertEqual(manifest["candidate_result_commit"], candidate.result_commit)
        self.assertEqual(manifest["integration_base_commit"], self.lab.base)
        self.assertIs(manifest["candidate_patch_unchanged"], True)
        self.assertTrue(manifest["integrator_session_id"].startswith("integrator-run:"))
        self.assertIn(
            {"gate": "grep -q alpha src/alpha.txt", "result": "pass"},
            manifest["affected_gates_rerun"],
        )
        # The candidate's own worktree still holds the unchanged reviewed commit.
        self.assertEqual(self.lab.git("rev-parse", "HEAD", cwd=candidate.worktree), candidate.result_commit)

    def test_second_disjoint_candidate_is_merged_and_both_candidates_stay_unchanged(self) -> None:
        """Protects fan-in: a later candidate merges with --no-ff, preserving both reviewed commits."""
        first = self.lab.candidate("T1", {"src/alpha.txt": "alpha\n"})
        second = self.lab.candidate("T2", {"src/beta.txt": "beta\n"})
        self.assertEqual(self.lab.cli("T1").code, 0)
        head_after_first = self.lab.branch_head()
        count_after_first = self.lab.commit_count()

        run = self.lab.cli("T2")

        self.assertEqual(run.code, 0, run.stderr)
        self.assertEqual(run.json["strategy"], "merge_no_ff")
        head = self.lab.branch_head()
        parents = self.lab.git("rev-list", "--parents", "-n1", head).split()[1:]
        self.assertEqual(parents, [head_after_first, second.result_commit])
        self.assertEqual(self.lab.commit_count(), count_after_first + 2)  # candidate + merge
        self.assertEqual(self.lab.tree_file("src/alpha.txt"), "alpha\n")
        self.assertEqual(self.lab.tree_file("src/beta.txt"), "beta\n")
        for candidate in (first, second):
            self.assertTrue(
                subprocess.run(
                    ["git", "merge-base", "--is-ancestor", candidate.result_commit, head],
                    cwd=self.lab.root,
                ).returncode == 0
            )
            self.assertEqual(
                self.lab.git("rev-parse", "HEAD", cwd=candidate.worktree), candidate.result_commit
            )
            binding = json.loads(self.lab.store.scalar(
                "SELECT payload_json FROM artifacts WHERE artifact_id = ?",
                (f"candidate-binding:{candidate.task_id}:{candidate.attempt_id}",),
            ))
            self.assertEqual(
                self.lab.git("rev-parse", f"{candidate.result_commit}^{{tree}}"), binding["tree_hash"]
            )
        (record,) = self.lab.artifacts("integration-record", "T2")
        self.assertEqual(record["payload"]["integration_commit"], head)
        self.assertEqual(record["payload"]["integration_base_commit"], head_after_first)

    def test_duplicate_request_is_a_noop_that_reports_the_existing_record(self) -> None:
        """Protects idempotency: repeating a completed integration never makes a second merge."""
        self.lab.candidate("T1", {"src/alpha.txt": "alpha\n"})
        self.lab.candidate("T2", {"src/beta.txt": "beta\n"})
        self.assertEqual(self.lab.cli("T1").code, 0)
        first = self.lab.cli("T2")
        head, count = self.lab.branch_head(), self.lab.commit_count()
        passed = len(self.lab.events("integration_passed"))

        again = self.lab.cli("T2")

        self.assertEqual(again.code, 0, again.stderr)
        self.assertEqual(again.json["outcome"], "already_integrated")
        self.assertEqual(again.json["integration_commit"], first.json["integration_commit"])
        self.assertEqual(self.lab.branch_head(), head)
        self.assertEqual(self.lab.commit_count(), count)
        self.assertEqual(len(self.lab.events("integration_passed")), passed)
        self.assertEqual(len(self.lab.artifacts("integration-record", "T2")), 1)

    def test_integration_branch_must_not_be_main_or_the_checked_out_branch(self) -> None:
        """Protects the user's checkout: integration never targets main or the current branch."""
        self.lab.candidate("T1", {"src/alpha.txt": "alpha\n"})
        for branch in ("main", self.lab.checkout_branch):
            with self.subTest(branch=branch):
                run = self.lab.cli("T1", "--branch", branch)
                self.assertEqual(run.code, 1)
                self.assertEqual(run.json["reason_code"], "integration_branch_invalid")
        self.assertIsNone(self.lab.branch_head())
        self.assertEqual(self.lab.state("T1"), "integration_ready")


class RejectionTests(IntegrationTestCase):
    def test_candidate_that_is_not_integration_ready_is_refused(self) -> None:
        """Protects the review gate: a candidate still awaiting review cannot be integrated."""
        self.lab.candidate("T1", {"src/alpha.txt": "alpha\n"}, review=False)
        self.assertEqual(self.lab.state("T1"), "independent_review")

        run = self.lab.cli("T1")

        self.assertEqual(run.code, 1)
        self.assertEqual(run.json["reason_code"], "task_not_integration_ready")
        self.assert_untouched("T1", head_before=None)

    def test_simulated_unreviewed_candidate_forced_to_integration_ready_is_rejected(self) -> None:
        """SIMULATED. Protects against state forged outside review.ingest: no review row, no integration."""
        self.lab.candidate("T1", {"src/alpha.txt": "alpha\n"}, review=False)
        self.lab.tamper("UPDATE tasks SET state = 'integration_ready' WHERE task_id = 'T1'")

        result = integrate("T1", store=self.lab.store)

        self.assert_rejected(result, "review_missing")
        self.assert_untouched("T1", head_before=None)
        (rejection,) = self.lab.artifacts("integration-rejection", "T1")
        self.assertFalse(rejection["payload"]["follow_up_auto_created"])
        self.assertEqual(rejection["payload"]["outcome"], "review_required")
        self.assertEqual(len(self.lab.events("integration_rejected", "T1")), 1)

    def test_candidate_amended_after_review_is_rejected_and_the_branch_is_untouched(self) -> None:
        """Protects the reviewed-patch invariant: an amended candidate invalidates the review."""
        candidate = self.lab.candidate("T1", {"src/alpha.txt": "alpha\n"})
        self.lab.git("commit", "--amend", "--quiet", "-m", "amended after review", cwd=candidate.worktree)
        self.assertNotEqual(self.lab.git("rev-parse", "HEAD", cwd=candidate.worktree), candidate.result_commit)

        result = integrate("T1", store=self.lab.store)

        self.assert_rejected(result, "candidate_modified_after_review")
        self.assert_untouched("T1", head_before=None)

    def test_candidate_replaced_with_different_content_after_review_is_rejected(self) -> None:
        """Protects against a swapped candidate: new content on the same worktree is never integrated."""
        candidate = self.lab.candidate("T1", {"src/alpha.txt": "alpha\n"})
        self.lab.git("reset", "--quiet", "--hard", candidate.base_commit, cwd=candidate.worktree)
        (candidate.worktree / "src").mkdir(exist_ok=True)
        (candidate.worktree / "src/alpha.txt").write_text("MALICIOUS\n")
        self.lab.git("add", "src/alpha.txt", cwd=candidate.worktree)
        self.lab.git("commit", "--quiet", "-m", "replacement", cwd=candidate.worktree)

        result = integrate("T1", store=self.lab.store)

        self.assert_rejected(result, "candidate_modified_after_review")
        self.assert_untouched("T1", head_before=None)
        self.assertIsNone(self.lab.tree_file("src/alpha.txt"))

    def test_simulated_stored_binding_that_disagrees_with_git_is_rejected(self) -> None:
        """SIMULATED. Protects against a doctored candidate-binding: the diff hash is recomputed from Git."""
        candidate = self.lab.candidate("T1", {"src/alpha.txt": "alpha\n"})
        self.lab.tamper(
            "UPDATE artifacts SET payload_json = json_set(payload_json, '$.diff_hash', ?) "
            "WHERE artifact_id = ?",
            ("f" * 64, f"candidate-binding:T1:{candidate.attempt_id}"),
        )

        result = integrate("T1", store=self.lab.store)

        self.assert_rejected(result, "candidate_binding_mismatch")
        self.assert_untouched("T1", head_before=None)

    def test_simulated_tampered_verification_evidence_is_rejected(self) -> None:
        """SIMULATED. Protects against edited evidence: the evidence hash is recomputed, not trusted."""
        candidate = self.lab.candidate("T1", {"src/alpha.txt": "alpha\n"}, checks=("true",))
        self.lab.tamper(
            "UPDATE artifacts SET payload_json = json_set(payload_json, '$.checks[0].exit_code', 1) "
            "WHERE artifact_id = ?",
            (f"verification:T1:{candidate.attempt_id}",),
        )

        result = integrate("T1", store=self.lab.store)

        self.assert_rejected(result, "verification_evidence_mismatch")
        self.assert_untouched("T1", head_before=None)

    def test_simulated_review_bound_to_other_evidence_is_rejected(self) -> None:
        """SIMULATED. Protects the review binding: a verdict for different evidence cannot authorize integration."""
        self.lab.candidate("T1", {"src/alpha.txt": "alpha\n"})
        self.lab.tamper(
            "UPDATE reviews SET payload_json = json_set(payload_json, '$.verification_evidence_hash', ?) "
            "WHERE task_id = 'T1'",
            ("a" * 64,),
        )

        result = integrate("T1", store=self.lab.store)

        self.assert_rejected(result, "review_binding_mismatch")
        self.assertIn("verification_evidence_hash", result["details"]["drifted"])
        self.assert_untouched("T1", head_before=None)

    def test_simulated_reviewer_identity_equal_to_author_is_rejected_again_at_integration(self) -> None:
        """SIMULATED. Protects separation of duty: independence is re-checked, not trusted from review time."""
        self.lab.candidate("T1", {"src/alpha.txt": "alpha\n"})
        self.lab.tamper(
            "UPDATE reviews SET reviewer_session_id = 'author-session-T1' WHERE task_id = 'T1'"
        )

        result = integrate("T1", store=self.lab.store)

        self.assert_rejected(result, "reviewer_not_independent")
        self.assert_untouched("T1", head_before=None)

    def test_integrator_identity_equal_to_author_or_reviewer_is_refused(self) -> None:
        """Protects separation of duty: the integrator may not be the author or the reviewer."""
        self.lab.candidate("T1", {"src/alpha.txt": "alpha\n"})
        for identity in ("author-session-T1", "author-run-T1", "reviewer-session-T1", "reviewer-run-T1"):
            with self.subTest(identity=identity):
                run = self.lab.cli("T1", "--integrator-id", identity)
                self.assertEqual(run.code, 1)
                self.assertEqual(run.json["reason_code"], "integrator_not_independent")
                self.assert_untouched("T1", head_before=None)
        ok = self.lab.cli("T1", "--integrator-id", "a-distinct-integrator-run")
        self.assertEqual(ok.code, 0, ok.stderr)

    def test_dependency_that_is_not_completed_is_rejected(self) -> None:
        """Protects topological order: a candidate cannot land before its required dependency."""
        self.lab.candidate("B-dep", {"src/dep.txt": "dep\n"})
        self.lab.candidate("A-main", {"src/main.txt": "main\n"})
        # SIMULATED: the dependency edge appears after A-main was built (a replan); there is no
        # public API to add one, and dispatch would refuse to lease a task with an open dependency.
        self.lab.tamper(
            "INSERT INTO dependencies(task_id, depends_on_task_id) VALUES ('A-main', 'B-dep')"
        )

        result = integrate("A-main", store=self.lab.store)

        self.assert_rejected(result, "dependency_incomplete")
        self.assertEqual(result["details"]["waiting_on"], ["B-dep"])
        self.assert_untouched("A-main", head_before=None)
        # The queue refuses to let the waiting task jump ahead, even though 'A-main' sorts first.
        order = []
        for _ in range(2):
            run = self.lab.cli()
            self.assertEqual(run.code, 0, run.stderr)
            order.append(run.json["task_id"])
        self.assertEqual(order, ["B-dep", "A-main"])
        self.assertEqual(self.lab.tree_file("src/dep.txt"), "dep\n")
        self.assertEqual(self.lab.tree_file("src/main.txt"), "main\n")

    def test_stale_interface_binding_is_rejected_for_replan_and_marks_the_task_stale(self) -> None:
        """Protects interface currency: a candidate built on a superseded binding is never integrated."""
        self.lab.candidate("T1", {"src/alpha.txt": "alpha\n"}, interface_pins={INTERFACE_ID: "1"})
        source = self.lab.root / INTERFACE_SOURCE
        source.write_text(source.read_text() + "\n")
        self.lab.scheduler.bump_interface(INTERFACE_ID, INTERFACE_SOURCE, "2")
        self.assertEqual(self.lab.state("T1"), "stale")  # the ordinary path already stales it ...
        # SIMULATED: ... so force the missed-propagation case this check exists for.
        self.lab.tamper("UPDATE tasks SET state = 'integration_ready' WHERE task_id = 'T1'")

        result = integrate("T1", store=self.lab.store)

        self.assert_rejected(result, "interface_binding_stale")
        self.assertEqual(result["rejection_outcome"], "replan_required")
        self.assertEqual(result["task_state"], "stale")  # via the legal dependency_changed transition
        self.assertIsNone(self.lab.branch_head())
        self.assertEqual(self.lab.leftover_integration_worktrees(), [])


class ConflictAndGateTests(IntegrationTestCase):
    def test_merge_conflict_is_rejected_for_repair_and_nothing_is_altered(self) -> None:
        """Protects the no-rewrite rule: a conflict is never resolved; branch and candidate stay byte-identical."""
        first = self.lab.candidate("T1", {"src/shared.txt": "one\n"})
        second = self.lab.candidate("T2", {"src/shared.txt": "two\n"})
        self.assertEqual(self.lab.cli("T1").code, 0)
        head = self.lab.branch_head()

        run = self.lab.cli("T2")

        self.assertEqual(run.code, 1)
        self.assertEqual(run.json["reason_code"], "conflict_repair_required")
        self.assertEqual(run.json["rejection_outcome"], "repair_required")
        self.assertEqual(run.json["details"]["conflicted_paths"], ["src/shared.txt"])
        self.assert_untouched("T2", head_before=head)
        self.assertEqual(self.lab.tree_file("src/shared.txt"), "one\n")
        self.assertEqual(self.lab.git("rev-parse", "HEAD", cwd=second.worktree), second.result_commit)
        self.assertEqual(self.lab.git("status", "--porcelain", "--untracked-files=no", cwd=second.worktree), "")
        self.assertEqual(self.lab.git("rev-parse", "HEAD", cwd=first.worktree), first.result_commit)
        self.assertEqual(self.lab.state("T2"), "integration_ready")  # no legal transition to repair_required
        (rejection,) = self.lab.artifacts("integration-rejection", "T2")
        self.assertIn("repair", rejection["payload"]["proposed_follow_up"])

    def test_rejected_head_is_parked_until_the_branch_moves_so_the_queue_is_not_wedged(self) -> None:
        """Protects AGENTS s9 (no retry without new evidence) and queue liveness behind a conflicted head."""
        self.lab.candidate("T1", {"src/shared.txt": "one\n"})
        self.lab.candidate("T2", {"src/shared.txt": "two\n"})
        self.lab.candidate("T3", {"src/other.txt": "other\n"})
        self.assertEqual(self.lab.cli("T1").code, 0)
        self.assertEqual(self.lab.cli("T2").code, 1)
        rejections = len(self.lab.artifacts("integration-rejection", "T2"))

        retry = self.lab.cli("T2")
        self.assertEqual(retry.code, 1)
        self.assertEqual(retry.json["reason_code"], "retry_without_new_evidence")
        self.assertEqual(len(self.lab.artifacts("integration-rejection", "T2")), rejections)

        nxt = self.lab.cli()  # the queue moves past the parked conflict
        self.assertEqual(nxt.code, 0, nxt.stderr)
        self.assertEqual(nxt.json["task_id"], "T3")

        again = self.lab.cli("T2")  # the branch moved: that is new evidence, so it is re-evaluated
        self.assertEqual(again.json["reason_code"], "conflict_repair_required")
        self.assertEqual(len(self.lab.artifacts("integration-rejection", "T2")), rejections + 1)

    def test_failing_affected_check_does_not_advance_the_branch_or_complete_the_task(self) -> None:
        """Protects the gate rerun: a check that fails only on the resulting tree blocks integration."""
        check = "test $(ls src | wc -l) -le 1"  # passes for each candidate alone, fails once both land
        self.lab.candidate("T1", {"src/one.txt": "1\n"}, checks=(check,))
        self.lab.candidate("T2", {"src/two.txt": "2\n"}, checks=(check,))
        self.assertEqual(self.lab.cli("T1").code, 0)
        head = self.lab.branch_head()

        run = self.lab.cli("T2")

        self.assertEqual(run.code, 1)
        self.assertEqual(run.json["reason_code"], "affected_check_failed")
        self.assert_untouched("T2", head_before=head)
        self.assertIsNone(self.lab.tree_file("src/two.txt"))
        (rejection,) = self.lab.artifacts("integration-rejection", "T2")
        failed = [g for g in rejection["payload"]["details"]["gates"] if g["result"] == "fail"]
        self.assertEqual([g["gate"] for g in failed], [check])
        self.assertEqual(failed[0]["exit_code"], 1)
        self.assertEqual(self.lab.state("T2"), "integration_ready")

    @mock.patch.dict(os.environ, {"AGENTCTL_CHECK_ISOLATION": "legacy-unsafe"})  # keys on the legacy gate tree path
    def test_hanging_check_is_a_recorded_failure_not_a_hang(self) -> None:
        """Protects liveness: a gate that hangs on the integration tree times out and fails."""
        # Quick in the candidate's own worktree, hangs only inside the integration worktree.
        hang = 'case "$PWD" in *agentctl-integration-gate-*) sleep 60;; esac'
        self.lab.candidate("T1", {"src/alpha.txt": "alpha\n"}, checks=(hang,))

        result = integrate("T1", store=self.lab.store, settings=IntegrationSettings(check_timeout=1.0))

        self.assert_rejected(result, "affected_check_failed")
        gate = result["details"]["gates"][-1]
        self.assertTrue(gate["timed_out"])
        self.assertIsNone(gate["exit_code"])
        self.assert_untouched("T1", head_before=None)

    @mock.patch.dict(os.environ, {"AGENTCTL_CHECK_ISOLATION": "legacy-unsafe"})  # keys on the legacy gate tree path
    def test_gate_environment_is_scrubbed_of_provider_and_credential_variables(self) -> None:
        """Protects secrets: checks on the integration tree see no CLAUDE_/ANTHROPIC_/credential variables."""
        # Strict only inside the integration worktree: scheduler.verify_candidate (Lane A's) still
        # runs the candidate's own checks with the inherited environment.
        check = (
            'case "$PWD" in *agentctl-integration-gate-*) test -z "$ANTHROPIC_API_KEY" '
            '&& test -z "$CLAUDE_CODE_OAUTH_TOKEN" && test -z "$AWS_SECRET_ACCESS_KEY";; esac'
        )
        self.lab.candidate("T1", {"src/alpha.txt": "alpha\n"}, checks=(check,))
        poisoned = {
            "ANTHROPIC_API_KEY": "sk-test-not-real",
            "CLAUDE_CODE_OAUTH_TOKEN": "tok-test-not-real",
            "AWS_SECRET_ACCESS_KEY": "aws-test-not-real",
        }
        with mock.patch.dict(os.environ, poisoned):
            result = integrate("T1", store=self.lab.store)

        self.assertEqual(result["outcome"], "integrated", result)
        self.assertEqual(self.lab.state("T1"), "completed")


class CliSurfaceTests(IntegrationTestCase):
    def test_unknown_task_and_empty_queue_exit_nonzero_with_distinct_codes(self) -> None:
        """Protects automation: scripts can tell 'nothing eligible' from 'refused'."""
        unknown = self.lab.cli("NO-SUCH-TASK")
        self.assertEqual((unknown.code, unknown.json["reason_code"]), (1, "task_unknown"))
        empty = self.lab.cli()
        self.assertEqual((empty.code, empty.json["reason_code"]), (3, "queue_empty"))
        usage = self.lab.cli("--bogus")
        self.assertEqual(usage.code, 2)


class ParkedMessageTests(IntegrationTestCase):
    def test_a_task_parked_for_a_canceled_dependency_is_told_so_not_to_wait_for_the_branch(self) -> None:
        """SIMULATED queue entry: the parked reason is supplied by the lifecycle code, not produced here.
        The refusal must say what is actually wrong; the generic text blames a branch that has not moved."""
        from types import SimpleNamespace

        from agentctl.integrate import IntegrationSettings, _Engine
        from agentctl.integration_queue import IntegrationRefused

        self.lab.candidate("T1", {"src/alpha.txt": "alpha\n"})
        engine = _Engine(self.lab.store, IntegrationSettings(), "integrator-test", None)
        for reason, wanted, unwanted in (
            ("dependency_canceled", "dependency was canceled", "has not moved"),
            ("conflict_repair_required", "has not moved", "canceled"),
        ):
            queue = SimpleNamespace(entry=lambda _task, r=reason: SimpleNamespace(parked_reason=r, waiting_on=()))
            with self.subTest(reason=reason), self.assertRaises(IntegrationRefused) as raised:
                engine._preflight_explicit("T1", queue)
            self.assertEqual(raised.exception.reason_code, "retry_without_new_evidence")
            self.assertIn(wanted, raised.exception.message)
            self.assertNotIn(unwanted, raised.exception.message)


if __name__ == "__main__":
    unittest.main()
