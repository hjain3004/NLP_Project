"""`agentctl run` end to end against a real repository, real git, and a real event store.

Provider behaviour is **simulated** by a fake ``claude`` executable (see
``helpers_lane_a``): these tests prove how the control plane routes, leases, audits,
commits, binds, and ends attempts for each provider outcome. They cannot prove a live
provider works; the opt-in LIVE test in ``test_claude_cli_adapter`` does that.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from agentctl.errors import PermissionViolation
from agentctl.execute import (
    MAX_FILE_BYTES,
    MAX_FILES,
    RunFailed,
    RunRefused,
    audit_proposal,
    run_task,
)

from .helpers_lane_a import LaneARepo, git, install_fake_claude, pid_alive, proposal

BOTH_ANTHROPIC = {
    "standard_implementation": ("anthropic", "claude-sonnet-5"),
    "complex_implementation": ("anthropic", "claude-sonnet-5"),
}


class RunTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.bin = tempfile.TemporaryDirectory()
        self.addCleanup(self.bin.cleanup)
        self.fake = install_fake_claude(pathlib.Path(self.bin.name), structured_output=proposal())
        patcher = mock.patch.dict(
            os.environ,
            {
                "PATH": f"{self.bin.name}{os.pathsep}{os.environ['PATH']}",
                "AGENTCTL_TEST_SECRET": "hunter2-sentinel-value",
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def repo(self, **kwargs) -> LaneARepo:
        kwargs.setdefault("registry", BOTH_ANTHROPIC)
        return LaneARepo(self, **kwargs)

    def run_it(self, repo: LaneARepo, **kwargs) -> dict:
        kwargs.setdefault("timeout_seconds", 60)
        kwargs.setdefault("max_budget_usd", 0.5)
        return run_task(repo.TASK_ID, store=repo.store, **kwargs)

    def assert_no_candidate(self, repo: LaneARepo) -> None:
        self.assertEqual(
            repo.store.scalar("SELECT COUNT(*) FROM artifacts WHERE artifact_type = 'candidate-binding'"), 0
        )
        self.assertEqual(
            repo.store.scalar("SELECT COUNT(*) FROM artifacts WHERE artifact_type = 'implementation-manifest' "
                              "AND integrable = 1"), 0
        )
        self.assertEqual(repo.store.scalar("SELECT COUNT(*) FROM leases WHERE active = 1"), 0)


class SuccessPathTests(RunTestCase):
    """SIMULATED provider; real registry-shaped routing, git worktree, store, and ingest."""

    def test_run_produces_a_candidate_bound_to_the_lease_and_provider_facts(self) -> None:
        """Protects: a returned proposal becomes a committed, correctly bound candidate with no hand-made return file."""
        repo = self.repo()
        result = self.run_it(repo)

        self.assertEqual(result["state"], "candidate")
        self.assertEqual(repo.state(), "candidate")
        lease = repo.store.rows("SELECT * FROM leases")[0]
        manifest = json.loads(repo.store.rows(
            "SELECT payload_json FROM artifacts WHERE artifact_type = 'implementation-manifest'"
        )[0]["payload_json"])
        self.assertEqual(
            (manifest["lease_id"], manifest["lease_epoch"], manifest["fencing_token"]),
            (lease["lease_id"], lease["epoch"], lease["fencing_token"]),
        )
        self.assertEqual(manifest["contract_hash"], repo.contract["identity"]["contract_hash"])
        self.assertEqual(manifest["base_commit"], repo.base_commit)
        self.assertEqual(manifest["result_commit"], result["result_commit"])
        # files_changed is derived from git, and the branch/worktree sit on the result commit.
        self.assertEqual(manifest["files_changed"], [{"path": "docs/lane-a/notes.md", "change_type": "modified"}])
        self.assertEqual(git(repo.root, "rev-parse", result["branch"]), result["result_commit"])
        worktree = repo.root / result["worktree"]
        self.assertEqual(git(worktree, "rev-parse", "HEAD"), result["result_commit"])
        self.assertEqual((worktree / "docs/lane-a/notes.md").read_text(), "line one\nline two\n")
        self.assertEqual(result["branch"], "agentctl/LANEA-001/attempt-1")
        # The model ran no commands; usage is the provider's, with input = input + cache creation + cache read.
        self.assertEqual(manifest["commands_executed"], [])
        self.assertEqual(
            manifest["usage"],
            {"input_tokens": 241, "output_tokens": 42, "latency_ms": 1234, "cost_usd": 0.0123},
        )
        self.assertEqual((manifest["provider"], manifest["model"]), ("anthropic", "claude-sonnet-5"))

    def test_commit_author_is_the_fixed_control_plane_identity(self) -> None:
        """Protects: a model cannot choose the commit author; identity comes from the control plane."""
        repo = self.repo()
        result = self.run_it(repo)
        author = git(repo.root, "log", "-1", "--format=%an <%ae>|%cn <%ce>", result["result_commit"])
        self.assertEqual(author, "agentctl control plane <agentctl@localhost.invalid>|"
                                 "agentctl control plane <agentctl@localhost.invalid>")

    def test_author_identity_is_the_minted_session_and_run_the_review_machinery_compares(self) -> None:
        """Protects: independence checks have a real author session/run to compare, with the provider's session equal to the minted one."""
        repo = self.repo()
        result = self.run_it(repo)
        attempt = repo.store.rows("SELECT * FROM attempts")[0]
        call = self.fake.calls()[0]
        passed_session = call["argv"][call["argv"].index("--session-id") + 1]
        self.assertEqual(attempt["author_session_id"], passed_session)
        self.assertEqual(attempt["author_session_id"], result["session_id"])
        self.assertEqual(attempt["author_run_id"], result["provider_run_id"])
        self.assertNotEqual(attempt["author_run_id"], result["cli_result_uuid"])
        self.assertEqual(json.loads(attempt["payload_json"])["author_actor_role"], "standard_implementation")
        run = repo.store.rows("SELECT * FROM model_runs")[0]
        self.assertEqual(run["run_id"], result["provider_run_id"])
        self.assertEqual(json.loads(run["usage_json"])["cache_read_input_tokens"], 30)

    def test_provider_is_invoked_with_the_fixed_toolless_argv_and_prompt_on_stdin(self) -> None:
        """Protects: proposal_only (no tools), registry model verbatim, no fallback model, prompt never in argv."""
        repo = self.repo()
        self.run_it(repo)
        (call,) = self.fake.calls()
        argv = call["argv"]
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--model") + 1], "claude-sonnet-5")
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        for required in ("-p", "--strict-mcp-config", "--disable-slash-commands", "--setting-sources",
                         "--json-schema", "--max-budget-usd", "--session-id"):
            self.assertIn(required, argv)
        for forbidden in ("--dangerously-skip-permissions", "--fallback-model", "--allowedTools",
                          "--mcp-config", "--add-dir"):
            self.assertNotIn(forbidden, argv)
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "0.5000")
        self.assertIn("original line", call["stdin"])  # readable file content is in the prompt
        self.assertIn("docs/lane-a/**", call["stdin"])
        self.assertFalse(any("original line" in part for part in argv))
        self.assertFalse(pathlib.Path(call["cwd"]).resolve().is_relative_to(repo.root))

    def test_run_record_has_evidence_and_no_secrets(self) -> None:
        """Protects: the per-attempt record carries provider/model/ids/usage but never the fencing token or environment values."""
        repo = self.repo()
        export = pathlib.Path(self.bin.name) / "exported.json"
        result = self.run_it(repo, export_path=str(export))
        record_path = repo.root / result["run_record"]
        self.assertEqual(record_path.read_text(), export.read_text())
        record = json.loads(record_path.read_text())
        self.assertEqual(record["provider_result"]["requested_model"], "claude-sonnet-5")
        self.assertEqual(record["provider_result"]["served_model"], "claude-sonnet-5")
        self.assertEqual(record["provider_result"]["usage_raw"]["cache_creation_input_tokens"], 200)
        self.assertEqual(record["identity"]["provider_run_id"], result["provider_run_id"])
        lease = repo.store.rows("SELECT fencing_token FROM leases")[0]
        everything = record_path.read_text() + json.dumps(
            [dict(row) for row in repo.store.rows("SELECT payload_json FROM events")]
            + [dict(row) for row in repo.store.rows("SELECT payload_json, usage_json FROM model_runs")]
        )
        self.assertNotIn(lease["fencing_token"], record_path.read_text())
        self.assertNotIn("hunter2-sentinel-value", everything)

    def test_helper_models_in_model_usage_are_flagged_not_adopted(self) -> None:
        """Protects: the 'actual model' stays the routed model even if the CLI also used a helper model."""
        repo = self.repo()
        self.fake.set(mode="extra_model", structured_output=proposal())
        result = self.run_it(repo)
        self.assertEqual(result["served_model"], "claude-sonnet-5")
        self.assertEqual(result["extra_models"], ["claude-helper-1"])
        record = json.loads((repo.root / result["run_record"]).read_text())
        self.assertTrue(record["provider_result"]["extra_models_flagged"])

    def test_cli_run_command_end_to_end(self) -> None:
        """Protects: `python3 -m agentctl run` is the ordinary entry point and prints one compact JSON result."""
        repo = self.repo()
        completed = repo.cli("run", repo.TASK_ID, "--timeout", "60", "--max-budget-usd", "0.5",
                             path_prefix=pathlib.Path(self.bin.name))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual((payload["outcome"], payload["state"]), ("candidate", "candidate"))
        self.assertEqual(repo.state(), "candidate")

    def test_cli_run_reports_a_provider_failure_nonzero_with_json(self) -> None:
        """Protects: a failed provider run exits nonzero with an actionable message and no success transition."""
        repo = self.repo()
        self.fake.set(mode="exit_nonzero")
        completed = repo.cli("run", repo.TASK_ID, path_prefix=pathlib.Path(self.bin.name))
        self.assertEqual(completed.returncode, 1)
        payload = json.loads(completed.stdout)
        self.assertEqual((payload["outcome"], payload["failure_class"]), ("failed", "nonzero_exit"))
        self.assertIn("nonzero_exit", completed.stderr)
        self.assertEqual(repo.state(), "ready")


class RefusalTests(RunTestCase):
    """The run is refused before any lease: nothing about the task changes."""

    def assert_refused_without_lease(self, repo: LaneARepo, status: str) -> None:
        with self.assertRaises(RunRefused) as raised:
            self.run_it(repo)
        self.assertEqual(raised.exception.status, status)
        self.assertEqual(repo.state(), "ready")
        self.assertEqual(repo.store.scalar("SELECT COUNT(*) FROM leases"), 0)
        self.assertEqual(self.fake.calls(), [])

    def test_real_registry_standard_task_is_refused_because_its_reviewer_is_unsupported(self) -> None:
        """Protects: reviewer floor. With the REAL registry, standard_implementation needs complex_implementation (openai): no lease, no substitution."""
        repo = self.repo(registry=None)
        with self.assertRaises(RunRefused) as raised:
            self.run_it(repo)
        self.assertEqual(raised.exception.status, "reviewer_unavailable")
        self.assertIn("complex_implementation", raised.exception.message)
        self.assertIn("openai", raised.exception.message)
        self.assertEqual(repo.state(), "ready")
        self.assertEqual(repo.store.scalar("SELECT COUNT(*) FROM leases"), 0)
        self.assertEqual(self.fake.calls(), [])

    def test_real_registry_complex_task_is_refused_as_unsupported_provider(self) -> None:
        """Protects: openai/google bindings yield an explicit non-available status and never fall back to another provider."""
        repo = self.repo(registry=None, capability_class="complex_implementation")
        self.assert_refused_without_lease(repo, "unsupported_provider")

    def test_missing_executable_refuses_before_leasing(self) -> None:
        """Protects: a missing CLI is an actionable refusal, not a consumed attempt."""
        repo = self.repo()
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        with mock.patch.dict(os.environ, {"PATH": empty.name}):
            with self.assertRaises(RunRefused) as raised:
                self.run_it(repo)
        self.assertEqual(raised.exception.status, "executable_missing")
        self.assertEqual(repo.store.scalar("SELECT COUNT(*) FROM leases"), 0)

    def test_unauthenticated_cli_refuses_before_leasing(self) -> None:
        """Protects: failed authentication blocks the run without consuming an attempt."""
        repo = self.repo()
        self.fake.set(logged_in=False, structured_output=proposal())
        self.assert_refused_without_lease(repo, "unauthenticated")

    def test_mechanical_class_above_low_risk_violates_the_policy_floor(self) -> None:
        """Protects: routing-policy hard floor for mechanical_execution (low risk only) is enforced before a lease."""
        repo = self.repo(
            capability_class="mechanical_execution", risk="medium",
            registry={**BOTH_ANTHROPIC, "mechanical_execution": ("anthropic", "claude-sonnet-5")},
        )
        self.assert_refused_without_lease(repo, "capability_floor")

    def test_only_a_ready_task_can_be_run(self) -> None:
        """Protects: a second run cannot re-execute a task that already holds a candidate."""
        repo = self.repo()
        self.run_it(repo)
        with self.assertRaises(RunRefused) as raised:
            self.run_it(repo)
        self.assertEqual(raised.exception.status, "not_ready")
        self.assertEqual(len(self.fake.calls()), 1)


class ProviderFailureTests(RunTestCase):
    """SIMULATED provider failures: every class ends the attempt without a success transition."""

    def test_infrastructure_failures_expire_the_lease_back_to_ready_and_record_the_run(self) -> None:
        """Protects: nothing-evaluated failures never create a candidate, release lease/locks, and are recorded in model_runs (usage unavailable, not zero)."""
        cases = {
            "exit_nonzero": "nonzero_exit",
            "unknown_model_exit0": "model_unavailable",
            "auth_error": "unauthenticated",
            "budget_error": "quota_or_budget",
            "model_mismatch": "model_unavailable",
        }
        repos = [self.repo(maximum_attempts=3), self.repo(maximum_attempts=3)]
        for index, (mode, expected) in enumerate(cases.items()):
            repo = repos[0 if index < 3 else 1]
            with self.subTest(mode=mode):
                self.fake.set(mode=mode, structured_output=proposal())
                with self.assertRaises(RunFailed) as raised:
                    self.run_it(repo)
                self.assertEqual(raised.exception.failure_class, expected)
                self.assertEqual(repo.state(), "ready")
                self.assert_no_candidate(repo)
                statuses = [row["status"] for row in repo.store.rows("SELECT status FROM model_runs")]
                self.assertEqual(statuses[-1], expected)
        repo = repos[0]
        # Unknown-model run: the provider reported no usage; the store holds an empty object, not zeros.
        unknown = repo.store.rows(
            "SELECT usage_json FROM model_runs WHERE status = 'model_unavailable' ORDER BY rowid LIMIT 1"
        )[0]
        self.assertEqual(json.loads(unknown["usage_json"]), {})

    def test_malformed_provider_output_goes_through_the_existing_schema_error_path(self) -> None:
        """Protects: unparseable output ends the attempt via handle_schema_error (repair_required here), never as a candidate."""
        for mode in ("malformed", "no_structured", "bad_session"):
            with self.subTest(mode=mode):
                repo = self.repo()
                self.fake.set(mode=mode, structured_output=proposal())
                with self.assertRaises(RunFailed) as raised:
                    self.run_it(repo)
                self.assertEqual(raised.exception.failure_class, "malformed_output")
                self.assertEqual(repo.state(), "repair_required")
                self.assert_no_candidate(repo)

    def test_session_id_not_equal_to_the_minted_one_is_rejected(self) -> None:
        """Protects: a result whose CLI session differs from the control-plane-minted session cannot supply the author identity."""
        repo = self.repo()
        self.fake.set(session_override="00000000-0000-4000-8000-000000000001", structured_output=proposal())
        with self.assertRaises(RunFailed) as raised:
            self.run_it(repo)
        self.assertEqual(raised.exception.failure_class, "malformed_output")
        self.assert_no_candidate(repo)

    def test_missing_usage_fails_the_attempt_instead_of_inventing_numbers(self) -> None:
        """Protects: if a required numeric manifest field is unavailable the attempt cannot produce a manifest (no zero-fill)."""
        repo = self.repo()
        self.fake.set(mode="missing_usage", structured_output=proposal())
        with self.assertRaises(RunFailed) as raised:
            self.run_it(repo)
        self.assertEqual(raised.exception.failure_class, "usage_unavailable")
        self.assertIn("schema proposal", raised.exception.message)
        self.assertEqual(repo.state(), "repair_required")
        self.assert_no_candidate(repo)
        self.assertEqual(git(repo.root, "rev-parse", "agentctl/LANEA-001/attempt-1"), repo.base_commit)

    def test_timeout_kills_the_whole_process_group_and_returns_the_task_to_ready(self) -> None:
        """Protects: a hung provider is killed with its grandchildren within the limit, partial output is kept, the lease is expired."""
        repo = self.repo()
        pid_file = pathlib.Path(self.bin.name) / "grandchild.pid"
        self.fake.set(mode="sleep", grandchild_pid_file=str(pid_file), structured_output=proposal())
        with self.assertRaises(RunFailed) as raised:
            self.run_it(repo, timeout_seconds=2)
        self.assertEqual(raised.exception.failure_class, "timeout")
        grandchild = int(pid_file.read_text())
        self.assertFalse(pid_alive(grandchild), "grandchild survived the attempt")
        self.assertEqual(repo.state(), "ready")
        self.assert_no_candidate(repo)
        record = json.loads((repo.root / raised.exception.payload["run_record"]).read_text())
        self.assertTrue(record["provider_result"]["timed_out"])
        self.assertIn("partial-output-before-kill", record["provider_result"]["output_excerpt"])

    def test_lease_expiring_during_the_run_leaves_orphan_evidence_and_no_candidate(self) -> None:
        """Protects: an artifact returned after its lease expired is retained as orphan evidence and advances nothing."""
        repo = self.repo()
        self.fake.set(expire_lease_db=str(repo.database), structured_output=proposal())
        with self.assertRaises(RunFailed) as raised:
            self.run_it(repo)
        self.assertEqual(raised.exception.failure_class, "contract_stale")
        self.assertEqual(repo.state(), "ready")
        self.assertEqual(repo.store.status(repo.TASK_ID)["orphan_evidence_count"], 1)
        self.assert_no_candidate(repo)
        orphan = repo.store.rows("SELECT integrable FROM artifacts WHERE artifact_type = 'implementation-manifest'")
        self.assertEqual([row["integrable"] for row in orphan], [0])


class ScopeAuditTests(RunTestCase):
    """SIMULATED model proposals that exceed scope never reach the worktree or a commit."""

    def assert_rejected_cleanly(self, repo: LaneARepo, escape_marker: pathlib.Path | None = None) -> None:
        with self.assertRaises(RunFailed) as raised:
            self.run_it(repo)
        self.assertEqual(raised.exception.failure_class, "permission_violation")
        self.assertEqual(repo.state(), "repair_required")
        self.assert_no_candidate(repo)
        branch = git(repo.root, "rev-parse", "agentctl/LANEA-001/attempt-1")
        self.assertEqual(branch, repo.base_commit)
        worktree = repo.root / "agent-system/runtime/worktrees/LANEA-001/attempt-1"
        self.assertEqual(git(worktree, "status", "--porcelain"), "")
        if escape_marker is not None:
            self.assertFalse(escape_marker.exists())

    def test_paths_outside_scope_are_rejected_before_any_write(self) -> None:
        """Protects: writable/prohibited paths, absolute paths, '..', globs, and duplicates are audited before writing."""
        cases = {
            "outside writable": [{"path": "docs/lane-a-outside.md", "action": "write", "content": "x\n"}],
            "parent traversal": [{"path": "docs/lane-a/../lane-a-outside.md", "action": "write", "content": "x\n"}],
            "absolute": [{"path": "/etc/agentctl-lane-a-test", "action": "write", "content": "x\n"}],
            "glob": [{"path": "docs/lane-a/*.md", "action": "write", "content": "x\n"}],
            "prohibited": [{"path": "agent-system/policies/new.json", "action": "write", "content": "{}\n"}],
            "git internals": [{"path": "docs/lane-a/.git/hooks/pre-commit", "action": "write", "content": "x\n"}],
            "duplicate": [
                {"path": "docs/lane-a/notes.md", "action": "write", "content": "a\n"},
                {"path": "docs/lane-a/NOTES.md", "action": "write", "content": "b\n"},
            ],
            "second file out of scope": [
                {"path": "docs/lane-a/notes.md", "action": "write", "content": "ok\n"},
                {"path": "docs/elsewhere.md", "action": "write", "content": "x\n"},
            ],
        }
        for name, files in cases.items():
            with self.subTest(case=name):
                repo = self.repo()
                self.fake.set(structured_output=proposal(files=files))
                self.assert_rejected_cleanly(repo)
                self.assertEqual((repo.root / "agent-system/runtime/worktrees/LANEA-001/attempt-1"
                                  / "docs/lane-a/notes.md").read_text(), "original line\n")

    def test_symlink_inside_the_writable_scope_cannot_be_used_to_escape(self) -> None:
        """Protects: writing through a committed symlink (directory or file) is rejected and nothing lands outside the worktree."""
        outside = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(outside, ignore_errors=True))
        repo = self.repo(symlink_target=outside)
        self.fake.set(structured_output=proposal(path="docs/lane-a/escape/pwned.md", content="x\n"))
        self.assert_rejected_cleanly(repo, outside / "pwned.md")
        self.assertEqual(list(outside.iterdir()), [])

    def test_a_proposal_that_changes_nothing_is_not_a_candidate(self) -> None:
        """Protects: an identical rewrite produces no commit and no candidate."""
        repo = self.repo()
        self.fake.set(structured_output=proposal(content="original line\n"))
        with self.assertRaises(RunFailed) as raised:
            self.run_it(repo)
        self.assertEqual(raised.exception.failure_class, "empty_candidate")
        self.assertEqual(repo.state(), "repair_required")
        self.assert_no_candidate(repo)

    def test_audit_limits_on_a_plain_directory(self) -> None:
        """Protects: oversize, binary, too-many-files, and non-existent deletes are rejected by the audit itself."""
        with tempfile.TemporaryDirectory() as directory:
            worktree = pathlib.Path(directory)
            (worktree / "docs").mkdir()
            (worktree / "docs" / "keep.md").write_text("x")
            kwargs = {"writable_paths": ("docs/**",), "prohibited_paths": (), "worktree": worktree}

            def files(*entries):
                return {"files": list(entries)}

            def write(path, content="x"):
                return {"path": path, "action": "write", "content": content}

            for label, payload in {
                "oversize": files(write("docs/big.md", "a" * (MAX_FILE_BYTES + 1))),
                "nul byte": files(write("docs/nul.md", "a\x00b")),
                "too many": files(*[write(f"docs/f{i}.md") for i in range(MAX_FILES + 1)]),
                "delete missing": files({"path": "docs/gone.md", "action": "delete", "content": ""}),
                "unrepresentable name": files(write("docs/has space.md")),
                "no files": {"files": []},
            }.items():
                with self.subTest(case=label), self.assertRaises(PermissionViolation):
                    audit_proposal(payload, **kwargs)
            planned = audit_proposal(
                files(write("docs/new.md"), {"path": "docs/keep.md", "action": "delete", "content": ""}), **kwargs
            )
            self.assertEqual([(c.path, c.action) for c in planned], [("docs/new.md", "write"), ("docs/keep.md", "delete")])


class DatabaseIntegrityTests(RunTestCase):
    def test_failed_run_leaves_events_that_explain_it(self) -> None:
        """Protects: the event store, not the run record, is the authority for why an attempt ended."""
        repo = self.repo()
        self.fake.set(mode="exit_nonzero", structured_output=proposal())
        with self.assertRaises(RunFailed):
            self.run_it(repo)
        types = [row["event_type"] for row in repo.store.rows(
            "SELECT event_type FROM events WHERE task_id = ? ORDER BY sequence", (repo.TASK_ID,)
        )]
        self.assertIn("model_run_recorded", types)
        self.assertEqual(types[-1], "lease_expired")
        reasons = [row["reason_code"] for row in repo.store.rows(
            "SELECT reason_code FROM events WHERE event_type = 'model_run_recorded'"
        )]
        self.assertEqual(reasons, ["nonzero_exit"])


if __name__ == "__main__":
    unittest.main()
