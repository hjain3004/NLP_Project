"""Repair authorization, supersession and cancellation, against real Git, a real event store and real processes.

Two fixtures are used and each test says which:

* ``LaneARepo`` + a **simulated** ``claude`` executable (``helpers_lane_a``): a real author run reaches
  ``repair_required`` through a blocking review, a failed check or a scope-audit failure, and the repaired
  ``run`` is a real ``run_task``. The provider is simulated; leasing, Git, the store and the transitions are real.
* ``Lab`` (``helpers_lane_b``): real ``integration_ready`` tasks and the real ``agentctl integrate`` command.

Crash points and races use real child processes. The crash *instant* and the interleaving are injected through a
hook (labelled simulated); the processes, SQLite, Git and the transitions are real.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from typing import Any
from unittest import mock

from agentctl.errors import ContractStale
from agentctl.execute import RunFailed, RunRefused, review_task, run_task
from agentctl.integration_queue import DEPENDENCY_CANCELED, QUEUE_INELIGIBLE_STATES, build_queue, store_query
from agentctl.lifecycle import (
    LifecycleRefused,
    authorize_repair,
    cancel_task,
    plan_cancel_chain,
    status_chain,
    supersede_task,
)
from agentctl.scheduler import TaskSpec
from agentctl.store import EventStore

from .helpers_lane_a import PACKAGE_PARENT, LaneARepo, install_fake_claude, proposal
from .helpers_lane_b import Lab

BOTH_ANTHROPIC = {
    "standard_implementation": ("anthropic", "claude-sonnet-5"),
    "complex_implementation": ("anthropic", "claude-sonnet-5"),
}
AUTHOR_CLAIM = "AUTHOR-CLAIM-PERSUASIVE-SUMMARY"
BLOCKING = {
    "finding_id": "R-1",
    "severity": "important",
    "violated_criterion": "acceptance behavior",
    "file": "docs/lane-a/notes.md",
    "location": "line 2",
    "reproducible_evidence": "REVIEWER-EVIDENCE-DIFF-SHOWS-NO-TRAILING-NEWLINE",
    "observed_behavior": "REVIEWER-OBSERVED-MISSING-NEWLINE",
    "required_behavior": "file ends with a newline",
    "required_outcome": "add the newline",
    "reviewer_confidence": "high",
    "blocking": True,
}

CHILD = r"""
import json, os, sys, time
from agentctl.lifecycle import LifecycleRefused, authorize_repair, supersede_task
from agentctl.store import EventStore

go = sys.argv[1]
while go != "-" and not os.path.exists(go):
    time.sleep(0.005)
mode = sys.argv[2]
store = EventStore()
try:
    if mode == "repair":
        result = authorize_repair(store, sys.argv[3], evidence=sys.argv[4], note="parallel", operator=sys.argv[5])
    else:
        point = sys.argv[6] if len(sys.argv) > 6 else ""
        marker = sys.argv[7] if len(sys.argv) > 7 else ""
        def hook(p):
            if p == point and point.startswith("after"):
                os._exit(97)
            if p == point and marker:
                open(marker, "w").write("planned")
                while not os.path.exists(marker + ".go"):
                    time.sleep(0.005)
        result = supersede_task(store, sys.argv[3], by=sys.argv[4], reason="child", operator=sys.argv[5], fault_hook=hook)
    print(json.dumps(result, sort_keys=True))
except LifecycleRefused as refused:
    print(json.dumps({"outcome": "refused", "reason_code": refused.reason_code}))
    sys.exit(3)
"""


def spawn(root: pathlib.Path, go: str, *args: str) -> subprocess.Popen[str]:
    env = {**os.environ, "PYTHONPATH": str(PACKAGE_PARENT)}
    return subprocess.Popen(
        [sys.executable, "-c", CHILD, go, *args], cwd=root, env=env, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )


def finish(process: subprocess.Popen[str], timeout: float = 120) -> tuple[int, dict[str, Any], str]:
    out, err = process.communicate(timeout=timeout)
    lines = [line for line in out.splitlines() if line.strip()]
    return process.returncode, (json.loads(lines[-1]) if lines else {}), err


def counts(store: EventStore) -> dict[str, int]:
    return {
        table: int(store.scalar(f"SELECT COUNT(*) FROM {table}"))
        for table in ("events", "artifacts", "attempts", "reviews", "model_runs", "tasks")
    }


def first_events(store: EventStore, limit: int) -> list[tuple[int, str]]:
    return [(r["sequence"], r["payload_json"]) for r in store.rows(
        "SELECT sequence, payload_json FROM events ORDER BY sequence LIMIT ?", (limit,))]


def events_for(store: EventStore, task_id: str, *types: str) -> list[dict[str, Any]]:
    sql = "SELECT sequence, event_type, reason_code, payload_json FROM events WHERE task_id = ?"
    params: list[Any] = [task_id]
    if types:
        sql += f" AND event_type IN ({','.join('?' for _ in types)})"
        params += list(types)
    return [{**dict(r), "payload": json.loads(r["payload_json"])} for r in store.rows(sql + " ORDER BY sequence", params)]


def chain_of(store: EventStore, task_id: str) -> list[tuple[str, str, str]]:
    admin = ("dependency_changed", "context_rebuilt", "contract_invalid", "replacement_plan_accepted")
    return [
        (e["event_type"], e["payload"]["prior_state"], e["payload"]["next_state"])
        for e in events_for(store, task_id, *admin)
    ]


FULL_CHAIN = [
    ("dependency_changed", "integration_ready", "stale"),
    ("context_rebuilt", "stale", "validated"),
    ("contract_invalid", "validated", "replan_required"),
    ("replacement_plan_accepted", "replan_required", "canceled"),
]


# --------------------------------------------------------------------------- #
# The chain, from the live transition table                                   #
# --------------------------------------------------------------------------- #


class ChainPlanningTests(unittest.TestCase):
    def test_shortest_legal_chain_per_source_state_and_the_states_with_none(self) -> None:
        """Protects the documented table: chains come from the real transition rows; running/integrating/terminal are refused with a reason."""
        walk = lambda state: [(s.event, s.to_state) for s in plan_cancel_chain(state)]  # noqa: E731
        self.assertEqual(walk("replan_required"), [("replacement_plan_accepted", "canceled")])
        self.assertEqual(walk("validated"), [("contract_invalid", "replan_required"),
                                             ("replacement_plan_accepted", "canceled")])
        self.assertEqual(walk("draft"), walk("validated"))
        self.assertEqual(walk("stale"), [("context_rebuilt", "validated"), ("contract_invalid", "replan_required"),
                                         ("replacement_plan_accepted", "canceled")])
        for state in ("ready", "candidate", "deterministic_verification", "independent_review",
                      "integration_ready", "repair_required", "blocked", "escalated"):
            with self.subTest(state=state):
                self.assertEqual([s.event for s in plan_cancel_chain(state)],
                                 ["dependency_changed", "context_rebuilt", "contract_invalid",
                                  "replacement_plan_accepted"])
        self.assertEqual(plan_cancel_chain("canceled"), [])
        for state in ("leased", "running", "integrating", "completed", "failed"):
            with self.subTest(state=state), self.assertRaises(LifecycleRefused) as raised:
                plan_cancel_chain(state)
            self.assertEqual(raised.exception.reason_code, f"state_{state}")
            self.assertTrue(raised.exception.message)

    def test_the_authority_row_is_never_used_by_an_operator(self) -> None:
        """Protects separation of duty: escalated is not shortcut to canceled through the architectural authority's row."""
        self.assertNotIn("authority_decision", [s.event for s in plan_cancel_chain("escalated")])


# --------------------------------------------------------------------------- #
# Repair                                                                      #
# --------------------------------------------------------------------------- #


class RepairCase(unittest.TestCase):
    def setUp(self) -> None:
        self.bin = tempfile.TemporaryDirectory()
        self.addCleanup(self.bin.cleanup)
        self.author_output = proposal(summary=AUTHOR_CLAIM, assumptions=[AUTHOR_CLAIM + "-ASSUMPTION"])
        self.fake = install_fake_claude(pathlib.Path(self.bin.name), structured_output=self.author_output)
        patcher = mock.patch.dict(os.environ, {"PATH": f"{self.bin.name}{os.pathsep}{os.environ['PATH']}"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def repo(self, **kwargs: Any) -> LaneARepo:
        kwargs.setdefault("registry", BOTH_ANTHROPIC)
        kwargs.setdefault("maximum_attempts", 3)
        return LaneARepo(self, **kwargs)

    def author(self, repo: LaneARepo) -> dict:
        self.fake.set(structured_output=self.author_output)
        return run_task(repo.TASK_ID, store=repo.store, timeout_seconds=60, max_budget_usd=0.5)

    def block_by_review(self, repo: LaneARepo) -> str:
        """author -> verify -> a (simulated) reviewer blocks it. Returns the review id."""
        self.author(repo)
        repo.scheduler.verify_candidate(repo.TASK_ID)
        self.fake.set(structured_output={"verdict": "blocking_findings", "findings": [BLOCKING]})
        review_task(repo.TASK_ID, store=repo.store, timeout_seconds=60, max_budget_usd=0.5)
        self.assertEqual(repo.state(), "repair_required")
        return str(repo.store.scalar("SELECT review_id FROM reviews ORDER BY rowid DESC LIMIT 1"))

    def remaining(self, repo: LaneARepo) -> int:
        return int(repo.store.scalar("SELECT attempts_remaining FROM budgets WHERE scope_id = ?", (repo.TASK_ID,)))

    def prompts(self) -> list[str]:
        return [call["stdin"] for call in self.fake.calls()]


class RepairHappyPathTests(RepairCase):
    def test_a_blocking_review_authorizes_a_repair_and_the_next_run_carries_the_reviewers_findings(self) -> None:
        """Protects: repair_required -> ready on new evidence, then a real `run` reaches candidate with the independent findings (not the author's claims) in its prompt."""
        repo = self.repo()
        review_id = self.block_by_review(repo)
        before = self.remaining(repo)
        events_before = repo.store.event_count()

        auth = authorize_repair(repo.store, repo.TASK_ID, evidence=review_id, note="Fix the trailing newline",
                                operator="alice@ops")

        self.assertEqual((auth["outcome"], auth["state"]), ("authorized", "ready"))
        self.assertEqual(repo.state(), "ready")
        self.assertEqual(self.remaining(repo), before, "authorizing must not touch the attempt budget")
        self.assertEqual(repo.store.event_count(), events_before + 1)
        (event,) = events_for(repo.store, repo.TASK_ID, "repair_authorized")
        self.assertEqual((event["payload"]["prior_state"], event["payload"]["next_state"]),
                         ("repair_required", "ready"))
        self.assertIn(auth["authorization_id"], event["payload"]["artifact_refs"])
        stored = json.loads(repo.store.scalar(
            "SELECT payload_json FROM artifacts WHERE artifact_type = 'repair-authorization'"))
        self.assertEqual(
            (stored["operator"], stored["note"], stored["previous_attempt_id"], stored["evidence"]["ref"],
             stored["evidence"]["kind"], stored["operator_identity_authenticated"]),
            ("alice@ops", "Fix the trailing newline", "LANEA-001-attempt-1", review_id, "review", False),
        )
        self.assertEqual(stored["feedback"]["blocking_findings"][0]["observed_behavior"], BLOCKING["observed_behavior"])

        result = self.author(repo)  # the repaired run
        self.assertEqual((result["state"], result["repair_authorization_id"]), ("candidate", auth["authorization_id"]))
        prompt = self.prompts()[-1]
        self.assertIn("THIS IS A REPAIR ATTEMPT", prompt)
        self.assertIn("REVIEWER-OBSERVED-MISSING-NEWLINE", prompt)
        self.assertIn("REVIEWER-EVIDENCE-DIFF-SHOWS-NO-TRAILING-NEWLINE", prompt)
        self.assertIn("Fix the trailing newline", prompt)
        self.assertIn("+line two", prompt)  # the rejected code, as a diff
        self.assertNotIn(AUTHOR_CLAIM, prompt)  # nothing the previous author said about its own work
        self.assertNotIn("THIS IS A REPAIR ATTEMPT", self.prompts()[0])
        self.assertEqual(self.remaining(repo), before - 1)  # the new attempt, not the authorization, spends it
        self.assertEqual(
            repo.store.scalar("SELECT COUNT(*) FROM attempts WHERE task_id = ?", (repo.TASK_ID,)), 2)
        record = json.loads((repo.root / result["run_record"]).read_text())
        self.assertEqual(record["repair"]["authorization_id"], auth["authorization_id"])
        self.assertTrue(record["repair"]["previous_diff_included"])
        self.assertNotIn("repair", json.loads(
            (repo.root / "agent-system/runtime/runs/LANEA-001/attempt-1.json").read_text()))

    def test_failed_verification_feeds_the_failed_check_not_the_author(self) -> None:
        """Protects: a verification failure is valid evidence; the feedback names the failed command and exit code only."""
        repo = self.repo(deterministic_checks=("test -f docs/lane-a/never-created.txt",))
        self.author(repo)
        repo.scheduler.verify_candidate(repo.TASK_ID)
        self.assertEqual(repo.state(), "repair_required")
        evidence = f"verification:{repo.TASK_ID}:{repo.TASK_ID}-attempt-1"

        auth = authorize_repair(repo.store, repo.TASK_ID, evidence=evidence, note="make the check pass")

        feedback = auth["feedback"]
        self.assertEqual(feedback["source"], "deterministic_verification")
        self.assertEqual(feedback["failed_checks"][0]["command"], "test -f docs/lane-a/never-created.txt")
        self.assertNotEqual(feedback["failed_checks"][0]["exit_code"], 0)
        self.author(repo)
        prompt = self.prompts()[-1]
        self.assertIn("never-created.txt", prompt)
        self.assertNotIn(AUTHOR_CLAIM, prompt)

    def test_an_ended_attempt_is_valid_evidence_for_a_failure_that_has_no_review(self) -> None:
        """Protects: a scope-audit failure leaves repair_required with no review; `attempt:<id>` is the evidence."""
        repo = self.repo()
        self.fake.set(structured_output=proposal(path="AppleBooks/secret.txt"))
        with self.assertRaises(RunFailed):
            run_task(repo.TASK_ID, store=repo.store, timeout_seconds=60, max_budget_usd=0.5)
        self.assertEqual(repo.state(), "repair_required")
        auth = authorize_repair(repo.store, repo.TASK_ID, evidence="attempt:LANEA-001-attempt-1",
                                note="stay inside docs/lane-a")
        self.assertEqual(auth["feedback"]["failure_reason"], "permission_violation")
        self.assertEqual(repo.state(), "ready")
        self.assertEqual(self.author(repo)["state"], "candidate")


class RepairRefusalTests(RepairCase):
    def assert_unchanged(self, repo: LaneARepo, before: dict[str, int], state: str) -> None:
        self.assertEqual(counts(repo.store), before)
        self.assertEqual(repo.state(), state)

    def test_refused_from_any_state_but_repair_required(self) -> None:
        """Protects: only the repair_required row exists; a ready task is not 'authorized' again."""
        repo = self.repo()
        before = counts(repo.store)
        with self.assertRaises(LifecycleRefused) as raised:
            authorize_repair(repo.store, repo.TASK_ID, evidence="whatever", note="n")
        self.assertEqual(raised.exception.reason_code, "wrong_state")
        self.assert_unchanged(repo, before, "ready")

    def test_never_extends_the_attempt_ceiling(self) -> None:
        """Protects AGENTS.md section 9: with no attempts left a repair is refused, and the budget is untouched."""
        repo = self.repo(maximum_attempts=1)
        review_id = self.block_by_review(repo)
        self.assertEqual(self.remaining(repo), 0)
        before = counts(repo.store)
        with self.assertRaises(LifecycleRefused) as raised:
            authorize_repair(repo.store, repo.TASK_ID, evidence=review_id, note="please")
        self.assertEqual(raised.exception.reason_code, "attempts_exhausted")
        self.assert_unchanged(repo, before, "repair_required")
        self.assertEqual(self.remaining(repo), 0)

    def test_same_evidence_twice_returns_the_existing_authorization(self) -> None:
        """Protects idempotence: no second authorization, no second event, no state change."""
        repo = self.repo()
        review_id = self.block_by_review(repo)
        first = authorize_repair(repo.store, repo.TASK_ID, evidence=review_id, note="one", operator="a")
        after_first = counts(repo.store)
        second = authorize_repair(repo.store, repo.TASK_ID, evidence=review_id, note="a different note", operator="b")
        self.assertEqual(second["outcome"], "already_authorized")
        self.assertEqual((second["authorization_id"], second["note"], second["operator"]),
                         (first["authorization_id"], "one", "a"))
        self.assertEqual(counts(repo.store), after_first)
        self.assertEqual(len(events_for(repo.store, repo.TASK_ID, "repair_authorized")), 1)

    def test_evidence_spent_on_an_earlier_attempt_cannot_justify_a_later_repair(self) -> None:
        """Protects 'retries without new evidence are forbidden': the old review id is refused for the next failure."""
        repo = self.repo()
        first_review = self.block_by_review(repo)
        authorize_repair(repo.store, repo.TASK_ID, evidence=first_review, note="one")
        second_review = self.block_by_review_again(repo)
        self.assertNotEqual(first_review, second_review)
        before = counts(repo.store)
        with self.assertRaises(LifecycleRefused) as raised:
            authorize_repair(repo.store, repo.TASK_ID, evidence=first_review, note="again")
        self.assertEqual(raised.exception.reason_code, "evidence_already_used")
        self.assert_unchanged(repo, before, "repair_required")
        second = authorize_repair(repo.store, repo.TASK_ID, evidence=second_review, note="two")
        self.assertEqual(second["sequence"], 2)
        self.assertEqual(self.remaining(repo), 1)

    def block_by_review_again(self, repo: LaneARepo) -> str:
        self.author(repo)
        repo.scheduler.verify_candidate(repo.TASK_ID)
        self.fake.set(structured_output={"verdict": "blocking_findings", "findings": [
            {**BLOCKING, "finding_id": "R-2", "observed_behavior": "SECOND-ROUND-FINDING"}]})
        review_task(repo.TASK_ID, store=repo.store, timeout_seconds=60, max_budget_usd=0.5)
        return str(repo.store.scalar("SELECT review_id FROM reviews ORDER BY rowid DESC LIMIT 1"))

    def test_evidence_must_be_real_and_about_the_latest_attempt_and_the_note_bounded(self) -> None:
        """Protects the evidence requirement: unknown, malformed, passing and over-long inputs are refused with nothing recorded."""
        repo = self.repo()
        review_id = self.block_by_review(repo)
        before = counts(repo.store)
        cases = (
            (dict(evidence="no-such-review", note="n"), "evidence_unknown"),
            (dict(evidence="has spaces", note="n"), "evidence_malformed"),
            (dict(evidence=f"verification:{repo.TASK_ID}:{repo.TASK_ID}-attempt-1", note="n"), "evidence_not_failing"),
            (dict(evidence=review_id, note="   "), "note_required"),
            (dict(evidence=review_id, note="x" * 1001), "note_too_long"),
        )
        for kwargs, reason in cases:
            with self.subTest(reason=reason), self.assertRaises(LifecycleRefused) as raised:
                authorize_repair(repo.store, repo.TASK_ID, **kwargs)
            self.assertEqual(raised.exception.reason_code, reason)
        self.assert_unchanged(repo, before, "repair_required")

    def test_cli_repair_reports_json_and_distinct_exit_codes(self) -> None:
        """Protects the operator surface: 0 authorized/idempotent, 3 refused, 2 usage."""
        repo = self.repo()
        review_id = self.block_by_review(repo)
        done = repo.cli("task", "repair", repo.TASK_ID, "--evidence", review_id, "--note", "n", "--operator", "bob")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["outcome"], "authorized")
        again = repo.cli("task", "repair", repo.TASK_ID, "--evidence", review_id, "--note", "n")
        self.assertEqual((again.returncode, json.loads(again.stdout)["outcome"]), (0, "already_authorized"))
        refused = repo.cli("task", "repair", repo.TASK_ID, "--evidence", "bogus", "--note", "n")
        self.assertEqual(refused.returncode, 3)
        self.assertEqual(json.loads(refused.stdout)["reason_code"], "wrong_state")  # the task is ready now
        self.assertEqual(repo.cli("task", "repair", repo.TASK_ID).returncode, 2)
        self.assertEqual(repo.cli("task", "bogus").returncode, 2)


class RepairConcurrencyTests(RepairCase):
    def test_parallel_identical_authorizations_produce_exactly_one(self) -> None:
        """Protects: six real processes race the same evidence; one authorization, one event, one state change, all callers get the same id."""
        repo = self.repo()
        review_id = self.block_by_review(repo)
        go = pathlib.Path(self.bin.name) / "go"
        children = [spawn(repo.root, str(go), "repair", repo.TASK_ID, review_id, f"op-{i}") for i in range(6)]
        time.sleep(0.5)  # let every interpreter reach its start barrier
        go.write_text("go")
        results = [finish(child) for child in children]

        for code, _payload, err in results:
            self.assertEqual(code, 0, err)
        outcomes = sorted(payload["outcome"] for _c, payload, _e in results)
        self.assertEqual(outcomes, ["already_authorized"] * 5 + ["authorized"])
        self.assertEqual(len({payload["authorization_id"] for _c, payload, _e in results}), 1)
        self.assertEqual(len(events_for(repo.store, repo.TASK_ID, "repair_authorized")), 1)
        self.assertEqual(repo.store.scalar("SELECT COUNT(*) FROM artifacts WHERE artifact_type = 'repair-authorization'"), 1)
        self.assertEqual(repo.state(), "ready")

    def test_a_repair_that_lands_first_beats_a_supersede_that_planned_earlier(self) -> None:
        """Protects 'exactly one wins' (SIMULATED interleaving, real processes): the supersede planned against repair_required, the repair commits, the supersede is refused and changes nothing."""
        repo = self.repo()
        review_id = self.block_by_review(repo)
        register_replacement(repo.scheduler, repo.TASK_ID, "LANEA-002")
        marker = pathlib.Path(self.bin.name) / "planned"
        child = spawn(repo.root, "-", "supersede", repo.TASK_ID, "LANEA-002", "op-s", "planned", str(marker))
        for _ in range(2000):
            if marker.exists():
                break
            time.sleep(0.01)
        self.assertTrue(marker.exists(), "the supersede never reached its planning point")

        authorize_repair(repo.store, repo.TASK_ID, evidence=review_id, note="repair wins")
        pathlib.Path(str(marker) + ".go").write_text("go")
        code, payload, err = finish(child)

        self.assertEqual(code, 3, err)
        self.assertEqual(payload["reason_code"], "state_changed")
        self.assertEqual(repo.state(), "ready")
        self.assertEqual(chain_of(repo.store, repo.TASK_ID), [])
        self.assertEqual(repo.store.scalar("SELECT COUNT(*) FROM artifacts WHERE artifact_type = 'task-cancellation'"), 0)

    def test_a_supersede_that_lands_first_makes_the_repair_wrong_state(self) -> None:
        """Protects the other order: after cancellation no repair can be authorized."""
        repo = self.repo()
        review_id = self.block_by_review(repo)
        register_replacement(repo.scheduler, repo.TASK_ID, "LANEA-002")
        supersede_task(repo.store, repo.TASK_ID, by="LANEA-002", reason="replaced")
        with self.assertRaises(LifecycleRefused) as raised:
            authorize_repair(repo.store, repo.TASK_ID, evidence=review_id, note="too late")
        self.assertEqual(raised.exception.reason_code, "wrong_state")
        self.assertEqual(repo.state(), "canceled")


def register_replacement(scheduler: Any, like_task_id: str, new_id: str, **overrides: Any) -> TaskSpec:
    """Register ``new_id`` as a copy of ``like_task_id``'s spec (a ready replacement task)."""
    spec = dataclasses.replace(scheduler.task_spec(like_task_id), task_id=new_id, **overrides)
    scheduler.register_task(spec)
    return spec


# --------------------------------------------------------------------------- #
# Supersession and cancellation: LaneARepo sources                            #
# --------------------------------------------------------------------------- #


class SupersedeFromRepairRequiredTests(RepairCase):
    def test_a_repair_required_task_is_superseded_to_canceled_and_stays_dead(self) -> None:
        """Protects: the legal chain from repair_required, and that nothing can revive the canceled task."""
        repo = self.repo()
        review_id = self.block_by_review(repo)
        register_replacement(repo.scheduler, repo.TASK_ID, "LANEA-002")
        before = counts(repo.store)

        result = supersede_task(repo.store, repo.TASK_ID, by="LANEA-002", reason="re-planned as LANEA-002",
                                operator="carol")

        self.assertEqual((result["outcome"], result["state"]), ("canceled", "canceled"))
        self.assertEqual(chain_of(repo.store, repo.TASK_ID),
                         [(e, "repair_required" if i == 0 else p, n) for i, (e, p, n) in enumerate(FULL_CHAIN)])
        self.assertEqual(
            {e["reason_code"] for e in events_for(repo.store, repo.TASK_ID, "dependency_changed", "context_rebuilt",
                                                  "contract_invalid", "replacement_plan_accepted")},
            {"superseded_by:LANEA-002"},
        )
        after = counts(repo.store)
        self.assertEqual(after["events"], before["events"] + 4)
        for table in ("artifacts", "attempts", "reviews", "model_runs"):
            self.assertGreaterEqual(after[table], before[table], table)
        self.assertEqual(after["artifacts"], before["artifacts"] + 1)  # only the decision record
        self.assertEqual(repo.store.scalar("SELECT COUNT(*) FROM leases WHERE active = 1"), 0)
        self.assertEqual(repo.store.scalar("SELECT COUNT(*) FROM semantic_locks"), 0)
        with self.assertRaises(RunRefused) as run:
            run_task(repo.TASK_ID, store=repo.store, timeout_seconds=60, max_budget_usd=0.5)
        self.assertEqual(run.exception.status, "not_ready")
        with self.assertRaises(LifecycleRefused):
            authorize_repair(repo.store, repo.TASK_ID, evidence=review_id, note="revive")

    def test_a_late_manifest_for_a_superseded_attempt_is_orphan_evidence_and_advances_nothing(self) -> None:
        """Protects AGENTS.md section 6: evidence of an ended attempt is retained as an orphan after cancellation, with no state change."""
        repo = self.repo()
        self.block_by_review(repo)
        manifest = json.loads(repo.store.scalar(
            "SELECT payload_json FROM artifacts WHERE artifact_type = 'implementation-manifest' AND integrable = 1"))
        register_replacement(repo.scheduler, repo.TASK_ID, "LANEA-002")
        supersede_task(repo.store, repo.TASK_ID, by="LANEA-002", reason="replaced")
        events_before = repo.store.event_count()
        self.assertEqual(repo.store.status(repo.TASK_ID)["orphan_evidence_count"], 0)

        with self.assertRaises(ContractStale):
            repo.scheduler.ingest_manifest(repo.TASK_ID, manifest)

        status = repo.store.status(repo.TASK_ID)
        self.assertEqual((status["state"], status["orphan_evidence_count"]), ("canceled", 1))
        self.assertEqual(repo.store.event_count(), events_before + 1)
        self.assertEqual(len(events_for(repo.store, repo.TASK_ID, "late_artifact")), 1)
        self.assertEqual(repo.store.scalar(
            "SELECT COUNT(*) FROM artifacts WHERE artifact_type = 'implementation-manifest' AND integrable = 0"), 1)
        self.assertEqual(repo.store.scalar(
            "SELECT COUNT(*) FROM artifacts WHERE artifact_type = 'implementation-manifest' AND integrable = 1"), 1)

    def test_running_and_leased_tasks_are_refused_naming_the_live_lease(self) -> None:
        """Protects: a task under a live author lease is never canceled out from under its provider call."""
        repo = self.repo()
        repo.scheduler.dispatch(repo.TASK_ID, worker_identity="x")
        for expected in ("leased", "running"):
            self.assertEqual(repo.state(), expected)
            before = counts(repo.store)
            with self.assertRaises(LifecycleRefused) as raised:
                cancel_task(repo.store, repo.TASK_ID, reason="stop")
            self.assertEqual(raised.exception.reason_code, f"state_{expected}")
            self.assertIn("lease", raised.exception.message)
            self.assertEqual(counts(repo.store), before)
            if expected == "leased":
                repo.scheduler.acknowledge(repo.TASK_ID, repo.scheduler.task_spec(repo.TASK_ID).context_hash)

    def test_cli_status_chain_shows_the_decision_and_the_remaining_plan(self) -> None:
        """Protects the read-only view: transitions, the recorded decision, and what a cancel would do."""
        repo = self.repo()
        self.block_by_review(repo)
        plan = json.loads(repo.cli("task", "status-chain", repo.TASK_ID).stdout)
        self.assertEqual(plan["state"], "repair_required")
        self.assertEqual([s["event"] for s in plan["cancel_plan_if_requested"]][-1], "replacement_plan_accepted")
        self.assertIsNone(plan["cancellation"])
        done = repo.cli("task", "cancel", repo.TASK_ID, "--reason", "not needed", "--operator", "dave")
        self.assertEqual(done.returncode, 0, done.stderr)
        view = json.loads(repo.cli("task", "status-chain", repo.TASK_ID).stdout)
        self.assertEqual(view["state"], "canceled")
        self.assertEqual((view["cancellation"]["mode"], view["cancellation"]["operator"]), ("cancel", "dave"))
        self.assertEqual([t["to"] for t in view["transitions"]][-4:], ["stale", "validated", "replan_required", "canceled"])
        self.assertEqual(view["cancel_plan_if_requested"], [])


# --------------------------------------------------------------------------- #
# Supersession and cancellation: integration_ready sources (Lab)              #
# --------------------------------------------------------------------------- #


def register(lab: Lab, task_id: str, *, dependencies: tuple[str, ...] = (), work_package: str = "WP-LANE-B") -> TaskSpec:
    digest = hashlib.sha256(task_id.encode()).hexdigest()
    spec = TaskSpec(
        task_id=task_id, work_package_id=work_package, contract_hash=digest,
        context_hash=hashlib.sha256(digest.encode()).hexdigest(), base_commit=lab.base,
        capability_class="standard_implementation", risk="low", enforcement_class="sandboxed_tools",
        writable_paths=(f"src/{task_id}.txt",), prohibited_paths=("AppleBooks/**",), semantic_locks={},
        maximum_attempts=2, maximum_artifact_corrections=1, dependencies=dependencies,
        deterministic_checks=("true",),
    )
    lab.scheduler.register_task(spec)
    return spec


def lab_cli(lab: Lab, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentctl", *args], cwd=lab.root,
        env={**os.environ, "PYTHONPATH": str(PACKAGE_PARENT)}, capture_output=True, text=True, timeout=120,
    )


class LabCase(unittest.TestCase):
    def setUp(self) -> None:
        self.lab = Lab()
        self.addCleanup(self.lab.close)
        self.store = self.lab.store
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)


class SupersedeFromIntegrationReadyTests(LabCase):
    def test_walks_the_four_existing_rows_one_event_each_and_keeps_all_history(self) -> None:
        """Protects: the chain, the per-step reason, atomic per-step events, released locks, and that no row or event is ever deleted."""
        self.lab.candidate("T-old", {"src/old.txt": "old\n"})
        register(self.lab, "T-new")
        before = counts(self.store)
        head = first_events(self.store, self.store.event_count())
        seen: list[int] = []

        result = supersede_task(self.store, "T-old", by="T-new", reason="replaced by T-new", operator="erin",
                                fault_hook=lambda p: seen.append(self.store.event_count()) if p.startswith("after_step") else None)

        self.assertEqual((result["outcome"], self.lab.state("T-old")), ("canceled", "canceled"))
        self.assertEqual(chain_of(self.store, "T-old"), FULL_CHAIN)
        events = events_for(self.store, "T-old", *[e for e, _p, _n in FULL_CHAIN])
        self.assertEqual({e["reason_code"] for e in events}, {"superseded_by:T-new"})
        for event in events:
            self.assertIn("task-cancellation:T-old", event["payload"]["artifact_refs"])
            self.assertIn("replacement:T-new", event["payload"]["artifact_refs"])
        decision = json.loads(self.store.scalar(
            "SELECT payload_json FROM artifacts WHERE artifact_id = 'task-cancellation:T-old'"))
        self.assertEqual((decision["mode"], decision["replacement_task_id"], decision["operator"],
                          decision["source_state"], decision["reason"]),
                         ("supersede", "T-new", "erin", "integration_ready", "replaced by T-new"))
        self.assertEqual(len(decision["planned_chain"]), 4)
        # History: monotonic event count at every step, nothing deleted, earlier events untouched.
        after = counts(self.store)
        self.assertEqual(seen, [before["events"] + n for n in (1, 2, 3, 4)],
                         "exactly one event is committed per step, strictly increasing")
        self.assertEqual(after["events"], before["events"] + 4)
        for table in ("artifacts", "attempts", "reviews", "model_runs", "tasks"):
            self.assertGreaterEqual(after[table], before[table], table)
        self.assertEqual(first_events(self.store, len(head)), head)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM leases WHERE active = 1"), 0)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM semantic_locks WHERE owner_task_id = 'T-old'"), 0)
        self.assertEqual(self.store.scalar(
            "SELECT COUNT(*) FROM artifacts WHERE task_id = 'T-old' AND artifact_type = 'implementation-manifest'"), 1)

    def test_demo_002_anomaly_a_rejected_task_is_retried_on_every_head_move_until_it_is_superseded(self) -> None:
        """Protects the demo anomaly: BEFORE supersession a rejected candidate re-enters the queue after the head moves; AFTER it, `integrate` never selects it and writes no further rejection."""
        # T-a passes `verify` (its worktree has a .git link) but fails at integration (a .git-less copy).
        self.lab.candidate("T-a", {"src/a.txt": "a\n"}, checks=("git rev-parse --show-toplevel",))
        self.lab.candidate("T-b", {"src/b.txt": "b\n"})
        self.lab.candidate("T-c", {"src/c.txt": "c\n"})
        rejections = lambda: len(self.lab.artifacts("integration-rejection", "T-a"))  # noqa: E731

        first = self.lab.cli()
        self.assertEqual((first.json["outcome"], first.json["task_id"]), ("rejected", "T-a"))
        self.assertEqual(self.lab.cli().json["task_id"], "T-b")  # the head moves
        retry = self.lab.cli()
        self.assertEqual((retry.json["outcome"], retry.json["task_id"]), ("rejected", "T-a"))  # THE ANOMALY
        self.assertEqual(rejections(), 2)

        supersede_task(self.store, "T-a", by="T-c", reason="DEMO-002 style: replaced")
        self.assertEqual(self.lab.state("T-a"), "canceled")
        self.assertEqual(self.lab.cli().json["task_id"], "T-c")  # the head moves again
        done = self.lab.cli()
        self.assertEqual((done.code, done.json["outcome"]), (3, "queue_empty"))
        self.assertNotIn("T-a", done.json["parked_without_new_evidence"])
        self.assertEqual(rejections(), 2, "a canceled task must not write further rejections")
        explicit = self.lab.cli("T-a")
        self.assertEqual((explicit.code, explicit.json["reason_code"]), (1, "task_not_integration_ready"))

    def test_stale_blocked_replan_and_canceled_tasks_are_never_queue_entries(self) -> None:
        """Protects queue eligibility: lifecycle-resolved tasks, and their dependents, are not eligible and not parked."""
        self.lab.candidate("T-old", {"src/old.txt": "old\n"})
        self.lab.candidate("T-ok", {"src/ok.txt": "ok\n"})
        register(self.lab, "T-new")
        register(self.lab, "D-dep", dependencies=("T-old",))
        supersede_task(self.store, "T-old", by="T-new", reason="replaced")
        queue = build_queue(store_query(self.store), None)
        self.assertEqual([e.task_id for e in queue.entries], ["T-ok"])
        self.assertEqual(self.lab.state("D-dep"), "stale")
        self.assertIn("canceled", QUEUE_INELIGIBLE_STATES)
        for state in ("stale", "blocked", "replan_required"):
            self.assertIn(state, QUEUE_INELIGIBLE_STATES)

    def test_a_candidate_whose_dependency_was_canceled_is_parked_not_retried(self) -> None:
        """Protects (SIMULATED cancellation of an already-integrated dependency): no endless 'waiting' rejections for a dependency that can never complete."""
        self.lab.candidate("T-pre", {"src/pre.txt": "pre\n"})
        self.assertEqual(self.lab.cli().json["task_id"], "T-pre")
        self.lab.candidate("T-dep", {"src/dep.txt": "dep\n"}, dependencies=("T-pre",))
        self.lab.tamper("UPDATE tasks SET state = 'canceled' WHERE task_id = 'T-pre'")  # simulated
        queue = build_queue(store_query(self.store), None)
        self.assertEqual(queue.entry("T-dep").parked_reason, DEPENDENCY_CANCELED)
        self.assertIsNone(queue.head)
        for _ in range(2):
            run = self.lab.cli()
            self.assertEqual(run.json["outcome"], "queue_empty")
        self.assertEqual(self.lab.artifacts("integration-rejection", "T-dep"), [])


class SupersedeDependentsTests(LabCase):
    def setUp(self) -> None:
        super().setUp()
        self.lab.candidate("T-old", {"src/old.txt": "old\n"})
        register(self.lab, "T-new")
        register(self.lab, "T-other")
        register(self.lab, "D1", dependencies=("T-old",))
        register(self.lab, "D2", dependencies=("T-old", "T-other"))
        register(self.lab, "D3", dependencies=("T-old", "T-new"))

    def deps(self, task_id: str) -> list[str]:
        return [r["depends_on_task_id"] for r in self.store.rows(
            "SELECT depends_on_task_id FROM dependencies WHERE task_id = ? ORDER BY depends_on_task_id", (task_id,))]

    def test_with_a_replacement_dependents_are_repointed_and_go_stale(self) -> None:
        """Protects: nothing is left depending on the canceled task; nonterminal dependents go stale so their context must be rebuilt."""
        result = supersede_task(self.store, "T-old", by="T-new", reason="replaced")
        self.assertEqual(self.deps("D1"), ["T-new"])
        self.assertEqual(self.deps("D2"), ["T-new", "T-other"])
        self.assertEqual(self.deps("D3"), ["T-new"], "an existing dependency on the replacement is merged, not duplicated")
        for dependent in ("D1", "D2", "D3"):
            self.assertEqual(self.lab.state(dependent), "stale")
            (event,) = events_for(self.store, dependent, "dependency_changed")
            self.assertEqual((event["reason_code"], event["payload"]["prior_state"], event["payload"]["next_state"]),
                             ("superseded_by:T-new", "ready", "stale"))
        self.assertEqual({d["task_id"]: d["dependency"] for d in result["dependents"]},
                         {"D1": "repointed", "D2": "repointed", "D3": "merged_into_existing_dependency"})
        self.assertEqual(self.store.scalar(
            "SELECT COUNT(*) FROM dependencies WHERE depends_on_task_id = 'T-old'"), 0)

    def test_without_a_replacement_dependents_are_blocked_and_cannot_look_ready(self) -> None:
        """Protects: a dependent of a canceled task is `blocked` via the existing dependency_unavailable row, never ready-looking."""
        result = cancel_task(self.store, "T-old", reason="no longer wanted")
        self.assertEqual(self.lab.state("T-old"), "canceled")
        for dependent in ("D1", "D2", "D3"):
            self.assertEqual(self.lab.state(dependent), "blocked")
            (event,) = events_for(self.store, dependent, "dependency_unavailable")
            self.assertEqual(event["reason_code"], "dependency_canceled:T-old")
        self.assertEqual(self.deps("D1"), ["T-old"], "rows keep naming the canceled task, so the block is explicit")
        ready = {row["task_id"]: row for row in self.lab.scheduler.ready()}
        self.assertFalse(ready["D1"]["ready"])
        self.assertEqual(ready["D1"]["reason_code"], "state_blocked")
        self.assertEqual({d["action"] for d in result["dependents"]}, {"dependency_unavailable"})

    def test_terminal_dependents_are_left_alone(self) -> None:
        """Protects: a completed dependent is not rewritten (SIMULATED terminal state)."""
        self.lab.tamper("UPDATE tasks SET state = 'completed' WHERE task_id = 'D1'")
        result = cancel_task(self.store, "T-old", reason="x")
        self.assertEqual(self.lab.state("D1"), "completed")
        self.assertEqual({d["task_id"]: d["action"] for d in result["dependents"]}["D1"], "skipped_terminal")

    def test_a_dependent_in_flight_refuses_the_whole_cancellation(self) -> None:
        """Protects atomicity of intent: refused before step 1, so nothing is half-canceled (SIMULATED leased dependent)."""
        self.lab.tamper("UPDATE tasks SET state = 'leased' WHERE task_id = 'D2'")
        before = counts(self.store)
        with self.assertRaises(LifecycleRefused) as raised:
            supersede_task(self.store, "T-old", by="T-new", reason="x")
        self.assertEqual(raised.exception.reason_code, "dependent_in_flight")
        self.assertEqual(counts(self.store)["events"], before["events"])
        self.assertEqual(self.lab.state("T-old"), "integration_ready")


class SupersedeRefusalTests(LabCase):
    def test_replacement_must_exist_share_the_work_package_and_not_be_terminal_or_dependent(self) -> None:
        """Protects the replacement rules: each violation is refused up front with nothing recorded."""
        self.lab.candidate("T-done", {"src/done.txt": "done\n"})
        self.assertEqual(self.lab.cli().json["task_id"], "T-done")  # T-done is now completed (terminal)
        self.lab.candidate("T-x", {"src/x.txt": "x\n"})
        register(self.lab, "T-other-wp", work_package="WP-OTHER")
        register(self.lab, "T-child", dependencies=("T-x",))
        before = counts(self.store)
        cases = (
            ("T-missing", "replacement_unknown"),
            ("T-x", "replacement_is_self"),
            ("T-other-wp", "replacement_other_work_package"),
            ("T-done", "replacement_terminal"),
            ("T-child", "replacement_depends_on_old"),
        )
        for replacement, reason in cases:
            with self.subTest(replacement=replacement), self.assertRaises(LifecycleRefused) as raised:
                supersede_task(self.store, "T-x", by=replacement, reason="x")
            self.assertEqual(raised.exception.reason_code, reason)
        self.assertEqual(counts(self.store), before)
        self.assertEqual(self.lab.state("T-x"), "integration_ready")

    def test_completed_and_integrating_tasks_are_refused_with_a_reason(self) -> None:
        """Protects: integrated work cannot be canceled; a task mid-integration (a real crash point) is not touched."""
        self.lab.candidate("T-done", {"src/done.txt": "done\n"})
        self.assertEqual(self.lab.cli().code, 0)
        with self.assertRaises(LifecycleRefused) as raised:
            cancel_task(self.store, "T-done", reason="oops")
        self.assertEqual(raised.exception.reason_code, "state_completed")

        self.lab.candidate("T-mid", {"src/mid.txt": "mid\n"})
        self.assertEqual(self.lab.run_until_killed("T-mid", "after_intent"), 97)  # SIMULATED crash instant
        self.assertEqual(self.lab.state("T-mid"), "integrating")
        before = counts(self.store)
        with self.assertRaises(LifecycleRefused) as raised:
            cancel_task(self.store, "T-mid", reason="oops")
        self.assertEqual(raised.exception.reason_code, "state_integrating")
        self.assertIn("integrat", raised.exception.message)
        self.assertEqual(counts(self.store), before)

    def test_repeating_a_finished_cancellation_is_a_no_op_and_a_different_decision_is_refused(self) -> None:
        """Protects idempotence and the recorded decision: replay changes nothing; another replacement cannot rewrite it."""
        self.lab.candidate("T-old", {"src/old.txt": "old\n"})
        register(self.lab, "T-new")
        register(self.lab, "T-other")
        supersede_task(self.store, "T-old", by="T-new", reason="first")
        after = counts(self.store)
        again = supersede_task(self.store, "T-old", by="T-new", reason="same decision, other words")
        self.assertEqual((again["outcome"], again["chain_executed_by_this_call"]), ("resumed_complete", []))
        self.assertEqual(counts(self.store), after)
        for call in (
            lambda: supersede_task(self.store, "T-old", by="T-other", reason="y"),
            lambda: cancel_task(self.store, "T-old", reason="y"),
        ):
            with self.assertRaises(LifecycleRefused) as raised:
                call()
            self.assertEqual(raised.exception.reason_code, "conflicting_decision")
        self.assertEqual(counts(self.store), after)

    def test_a_task_canceled_by_other_means_is_reported_not_rewritten(self) -> None:
        """Protects: a canceled task with no lifecycle record is a no-op that says so (SIMULATED prior cancellation)."""
        self.lab.candidate("T-old", {"src/old.txt": "old\n"})
        self.lab.tamper("UPDATE tasks SET state = 'canceled' WHERE task_id = 'T-old'")
        before = counts(self.store)
        result = cancel_task(self.store, "T-old", reason="x")
        self.assertEqual((result["outcome"], result["lifecycle_record"]), ("already_canceled", False))
        self.assertEqual(counts(self.store), before)


class InterruptedAndConcurrentTests(LabCase):
    def setUp(self) -> None:
        super().setUp()
        self.lab.candidate("T-old", {"src/old.txt": "old\n"})
        register(self.lab, "T-new")
        register(self.lab, "D1", dependencies=("T-old",))

    def test_a_supersede_killed_after_step_two_resumes_and_finishes_each_step_once(self) -> None:
        """Protects resumability (SIMULATED crash instant, real process and store): rerunning completes only the remaining steps and then the dependents."""
        child = spawn(self.lab.root, "-", "supersede", "T-old", "T-new", "op-1", "after_step:2")
        code, _payload, err = finish(child)
        self.assertEqual(code, 97, err)
        self.assertEqual(self.lab.state("T-old"), "validated")
        self.assertEqual(len(chain_of(self.store, "T-old")), 2)
        self.assertEqual(self.lab.state("D1"), "ready", "dependents are untouched until the task itself is canceled")

        rerun = lab_cli(self.lab, "task", "supersede", "T-old", "--by", "T-new", "--reason", "resume")
        self.assertEqual(rerun.returncode, 0, rerun.stderr)
        result = json.loads(rerun.stdout)
        self.assertEqual([s["event"] for s in result["chain_executed_by_this_call"]],
                         ["contract_invalid", "replacement_plan_accepted"])
        self.assertTrue(result["resumed"])
        self.assertEqual(self.lab.state("T-old"), "canceled")
        self.assertEqual(chain_of(self.store, "T-old"), FULL_CHAIN)
        self.assertEqual(self.lab.state("D1"), "stale")
        self.assertEqual(len(events_for(self.store, "D1", "dependency_changed")), 1)

    def test_a_supersede_killed_between_cancellation_and_dependents_still_resolves_them_on_rerun(self) -> None:
        """Protects: the dependents step is resumable on its own (SIMULATED crash instant)."""
        code, _payload, err = finish(spawn(self.lab.root, "-", "supersede", "T-old", "T-new", "op-1", "after_cancel"))
        self.assertEqual(code, 97, err)
        self.assertEqual((self.lab.state("T-old"), self.lab.state("D1")), ("canceled", "ready"))
        rerun = lab_cli(self.lab, "task", "supersede", "T-old", "--by", "T-new", "--reason", "resume")
        self.assertEqual(rerun.returncode, 0, rerun.stderr)
        self.assertEqual(self.lab.state("D1"), "stale")
        self.assertEqual(len(chain_of(self.store, "T-old")), 4)

    def test_a_different_replacement_cannot_hijack_a_half_finished_supersede(self) -> None:
        """Protects the recorded decision across a crash."""
        register(self.lab, "T-newer")
        finish(spawn(self.lab.root, "-", "supersede", "T-old", "T-new", "op-1", "after_step:1"))
        rerun = lab_cli(self.lab, "task", "supersede", "T-old", "--by", "T-newer", "--reason", "hijack")
        self.assertEqual(rerun.returncode, 3)
        self.assertEqual(json.loads(rerun.stdout)["reason_code"], "conflicting_decision")
        self.assertEqual(self.lab.state("T-old"), "stale")

    def test_parallel_identical_supersedes_walk_each_step_and_resolve_each_dependent_exactly_once(self) -> None:
        """Protects: four real processes race one supersede; the chain, the decision record and the dependent each exist once."""
        go = pathlib.Path(self.tmp.name) / "go"
        children = [spawn(self.lab.root, str(go), "supersede", "T-old", "T-new", f"op-{i}") for i in range(4)]
        time.sleep(0.5)
        go.write_text("go")
        results = [finish(child) for child in children]
        for code, payload, err in results:
            self.assertEqual(code, 0, err + json.dumps(payload))
            self.assertEqual(payload["state"], "canceled")
        self.assertEqual(chain_of(self.store, "T-old"), FULL_CHAIN)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM artifacts WHERE artifact_id = 'task-cancellation:T-old'"), 1)
        self.assertEqual(len(events_for(self.store, "D1", "dependency_changed")), 1)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM artifacts WHERE artifact_type = 'dependent-resolution'"), 1)
        self.assertEqual(self.lab.state("D1"), "stale")

    def test_parallel_supersedes_with_different_replacements_have_exactly_one_winner(self) -> None:
        """Protects: two operators race different replacements; one decision is recorded and the loser changes nothing."""
        register(self.lab, "T-newer")
        go = pathlib.Path(self.tmp.name) / "go"
        children = [spawn(self.lab.root, str(go), "supersede", "T-old", by, f"op-{by}") for by in ("T-new", "T-newer")]
        time.sleep(0.5)
        go.write_text("go")
        results = sorted((finish(child) for child in children), key=lambda r: r[0])
        self.assertEqual([r[0] for r in results], [0, 3], [r[2] for r in results])
        winner = results[0][1]["replacement_task_id"]
        decision = json.loads(self.store.scalar(
            "SELECT payload_json FROM artifacts WHERE artifact_id = 'task-cancellation:T-old'"))
        self.assertEqual(decision["replacement_task_id"], winner)
        self.assertEqual({e["reason_code"] for e in events_for(self.store, "T-old", *[e for e, _p, _n in FULL_CHAIN])},
                         {f"superseded_by:{winner}"})
        self.assertEqual(chain_of(self.store, "T-old"), FULL_CHAIN)
        self.assertEqual(self.lab.state("T-old"), "canceled")

    def test_status_chain_command_reports_dependents_and_the_decision(self) -> None:
        """Protects the operator view for a Lab task, via the real CLI."""
        done = lab_cli(self.lab, "task", "supersede", "T-old", "--by", "T-new", "--reason", "r", "--operator", "frank")
        self.assertEqual(done.returncode, 0, done.stderr)
        view = json.loads(lab_cli(self.lab, "task", "status-chain", "T-old").stdout)
        self.assertEqual(view["state"], "canceled")
        self.assertEqual(view["cancellation"]["replacement_task_id"], "T-new")
        self.assertEqual(view["dependents"], [])  # re-pointed away from the canceled task
        dependent = json.loads(lab_cli(self.lab, "task", "status-chain", "D1").stdout)
        self.assertEqual(dependent["state"], "stale")
        self.assertEqual(dependent["transitions"][-1]["reason_code"], "superseded_by:T-new")


if __name__ == "__main__":
    unittest.main()
