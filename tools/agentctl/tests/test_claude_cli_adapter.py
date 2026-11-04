"""Claude CLI adapter: exact invocation, strict result classification, process-group lifetime.

Tests labelled SIMULATED drive a fake ``claude`` executable (``helpers_lane_a``) to inject
provider outcomes deterministically; they prove the adapter's handling, not that a live
provider works. The single LIVE test is opt-in (``AGENTCTL_LIVE_PROVIDER=1``) and is the only
evidence of live authentication, model access, invocation, and result collection.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import tempfile
import unittest
import uuid

from agentctl.adapters.claude_cli import ClaudeCliAdapter, build_argv

from .helpers_lane_a import SOURCE_ROOT, install_fake_claude, pid_alive

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer"],
    "properties": {"answer": {"type": "string"}},
}


class AdapterTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.fake = install_fake_claude(pathlib.Path(self.directory.name), structured_output={"answer": "pong"})
        self.adapter = ClaudeCliAdapter("claude-test-1", executable=str(self.fake.executable), timeout_seconds=30)

    def invoke(self, **kwargs):
        kwargs.setdefault("schema", SCHEMA)
        return self.adapter.invoke("PROMPT-TEXT", **kwargs)


class InvocationTests(AdapterTestCase):
    """SIMULATED provider."""

    def test_exact_argv_is_toolless_and_prompt_travels_on_stdin(self) -> None:
        """Protects: proposal_only (`--tools ""`), registry model verbatim, no permission bypass or fallback model, prompt not in argv."""
        session = str(uuid.uuid4())
        result = self.invoke(session_id=session, max_budget_usd=0.25)
        self.assertTrue(result.ok, result.failure_detail)
        (call,) = self.fake.calls()
        self.assertEqual(
            call["argv"],
            [
                "-p", "--tools", "", "--output-format", "json", "--model", "claude-test-1",
                "--setting-sources", "", "--disable-slash-commands", "--strict-mcp-config",
                "--permission-mode", "dontAsk", "--session-id", session,
                "--json-schema", json.dumps(SCHEMA, sort_keys=True, separators=(",", ":")),
                "--max-budget-usd", "0.2500",
            ],
        )
        self.assertEqual(call["stdin"], "PROMPT-TEXT")
        self.assertEqual(
            build_argv("claude", "claude-test-1", SCHEMA, 0.25, session)[1:], call["argv"]
        )

    def test_child_runs_outside_the_repository_with_environment_passed_unchanged(self) -> None:
        """Protects: a repo CLAUDE.md cannot leak into the worker; the environment is passed through (names only observed here)."""
        os.environ["AGENTCTL_TEST_PASSTHROUGH"] = "x"
        self.addCleanup(os.environ.pop, "AGENTCTL_TEST_PASSTHROUGH", None)
        self.invoke()
        (call,) = self.fake.calls()
        self.assertFalse(pathlib.Path(call["cwd"]).is_relative_to(SOURCE_ROOT))
        self.assertIn("AGENTCTL_TEST_PASSTHROUGH", call["env_names"])

    def test_success_captures_ids_served_model_and_usage_without_inventing_numbers(self) -> None:
        """Protects: session/result ids, served model, summed input tokens, raw usage, latency and cost are captured as reported."""
        session = str(uuid.uuid4())
        self.fake.set(structured_output={"answer": "pong"}, result_uuid="abc-result-uuid")
        result = self.invoke(session_id=session)
        self.assertTrue(result.ok)
        self.assertEqual((result.session_id, result.result_uuid), (session, "abc-result-uuid"))
        self.assertEqual((result.requested_model, result.served_model), ("claude-test-1", "claude-test-1"))
        self.assertEqual(result.input_tokens, 11 + 200 + 30)
        self.assertEqual((result.output_tokens, result.latency_ms, result.cost_usd), (42, 1234, 0.0123))
        self.assertEqual(result.usage_raw["cache_read_input_tokens"], 30)
        self.assertEqual(result.exit_status, 0)

    def test_adapter_contract_run_uses_the_same_invocation(self) -> None:
        """Protects: the provider-neutral Adapter.run signature works and yields a typed RunResult."""
        result = self.adapter.run(
            "standard_implementation",
            {"attempt_id": "T-attempt-1", "context_hash": "c" * 64},
            {"identity": {"task_id": "T", "contract_hash": "d" * 64}},
            {"tool_allowlist": []},
            {"timeout_seconds": 30, "max_budget_usd": 0.1},
            SCHEMA,
        )
        self.assertEqual((result.status, result.provider, result.model), ("completed", "anthropic", "claude-test-1"))
        self.assertEqual((result.task_id, result.attempt_id, result.contract_hash), ("T", "T-attempt-1", "d" * 64))


class FailureClassificationTests(AdapterTestCase):
    """SIMULATED provider failures: each has its own explicit class and never looks like success."""

    def expect(self, failure_class: str, **scenario):
        self.fake.set(**{"structured_output": {"answer": "pong"}, **scenario})
        result = self.invoke()
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_class, failure_class, result.failure_detail)
        self.assertIsNone(result.structured_output if failure_class != "usage_unavailable" else None)
        return result

    def test_unknown_model_with_exit_zero_and_success_subtype_is_model_unavailable(self) -> None:
        """Protects: neither exit status nor `subtype: success` is trusted (observed live for unknown models)."""
        result = self.expect("model_unavailable", mode="unknown_model_exit0")
        self.assertEqual((result.exit_status, result.subtype, result.is_error), (0, "success", True))

    def test_served_model_not_the_routed_model_is_model_unavailable(self) -> None:
        """Protects: a result served by a different model is rejected, never adopted."""
        self.expect("model_unavailable", mode="model_mismatch")

    def test_authentication_failure(self) -> None:
        """Protects: is_error with a 401 is reported as unauthenticated, with an actionable message."""
        result = self.expect("unauthenticated", mode="auth_error")
        self.assertIn("login", result.failure_detail)

    def test_budget_exhaustion_is_quota_or_budget_and_keeps_the_incurred_cost(self) -> None:
        """Protects: --max-budget-usd exhaustion is classified distinctly and cost already spent is not lost."""
        result = self.expect("quota_or_budget", mode="budget_error", exit_code=1)
        self.assertEqual(result.cost_usd, 0.0123)

    def test_nonzero_exit_without_json(self) -> None:
        """Protects: a crash is nonzero_exit, with stderr captured for diagnosis."""
        result = self.expect("nonzero_exit", mode="exit_nonzero")
        self.assertEqual(result.exit_status, 7)
        self.assertIn("crashed", result.stderr_excerpt)

    def test_non_json_stdout_with_exit_zero_is_malformed(self) -> None:
        """Protects: strict JSON parsing; garbage on stdout is never coerced."""
        self.expect("malformed_output", mode="malformed")

    def test_missing_structured_output_is_malformed(self) -> None:
        """Protects: a free-text answer without the schema-bound structured_output is not a proposal."""
        self.expect("malformed_output", mode="no_structured")

    def test_structured_output_violating_the_schema_is_malformed(self) -> None:
        """Protects: output is validated against the requested schema, not just present."""
        self.expect("malformed_output", structured_output={"unexpected": 1})

    def test_invalid_session_id_is_malformed(self) -> None:
        """Protects: the CLI-reported session id must be a valid UUID."""
        self.expect("malformed_output", mode="bad_session")

    def test_session_id_differing_from_the_minted_one_is_malformed(self) -> None:
        """Protects: provenance; the result must come from the session the control plane started."""
        self.fake.set(structured_output={"answer": "pong"}, session_override=str(uuid.uuid4()))
        result = self.invoke(session_id=str(uuid.uuid4()))
        self.assertEqual(result.failure_class, "malformed_output")

    def test_incomplete_terminal_reason_is_not_success(self) -> None:
        """Protects: terminal_reason must be 'completed'."""
        self.expect("malformed_output", mode="not_completed")

    def test_missing_usage_field_is_usage_unavailable_not_zero(self) -> None:
        """Protects: unreported usage stays None; the attempt fails and says a schema proposal is needed."""
        result = self.expect("usage_unavailable", mode="missing_usage")
        self.assertIsNone(result.input_tokens)
        self.assertEqual(result.output_tokens, 42)
        self.assertIn("schema proposal", result.failure_detail)

    def test_usage_may_be_absent_when_the_caller_does_not_require_it(self) -> None:
        """Protects: reviewer runs (no usage field in the verdict) are not failed for lacking cache counters."""
        self.fake.set(mode="missing_usage", structured_output={"answer": "pong"})
        result = self.invoke(require_usage=False)
        self.assertTrue(result.ok)
        self.assertIsNone(result.input_tokens)

    def test_missing_executable(self) -> None:
        """Protects: a vanished CLI is executable_missing, not a crash of the control plane."""
        result = ClaudeCliAdapter("m", executable=str(self.fake.directory / "nope")).invoke("p", schema=SCHEMA)
        self.assertEqual(result.failure_class, "executable_missing")


class ProcessLifetimeTests(AdapterTestCase):
    """SIMULATED hang; the process handling itself is real."""

    def test_timeout_kills_the_process_group_including_a_grandchild_and_keeps_partial_output(self) -> None:
        """Protects: a hung provider cannot outlive its wall-clock limit or leave descendants behind."""
        pid_file = pathlib.Path(self.directory.name) / "gc.pid"
        self.fake.set(mode="sleep", grandchild_pid_file=str(pid_file), structured_output={"answer": "x"})
        result = self.invoke(timeout_seconds=1.5)
        self.assertEqual(result.failure_class, "timeout")
        self.assertTrue(result.timed_out)
        self.assertLess(result.wall_ms, 15000)
        self.assertFalse(pid_alive(int(pid_file.read_text())), "grandchild survived")
        self.assertIn("partial-output-before-kill", result.output_excerpt)


@unittest.skipUnless(os.environ.get("AGENTCTL_LIVE_PROVIDER") == "1", "LIVE test: set AGENTCTL_LIVE_PROVIDER=1")
class LiveSmokeTests(unittest.TestCase):
    """LIVE: calls the real `claude` CLI once (model claude-haiku-5-5, hard $0.05 cap)."""

    def test_live_claude_cli_returns_real_ids_and_usage(self) -> None:
        """Protects: authentication, model access, invocation, and result collection are real, not simulated."""
        executable = shutil.which("claude")
        self.assertIsNotNone(executable, "claude CLI not on PATH")
        session = str(uuid.uuid4())
        result = ClaudeCliAdapter("claude-haiku-5-5", executable=executable, timeout_seconds=120).invoke(
            'Return the JSON object {"answer": "pong"}.',
            schema=SCHEMA,
            max_budget_usd=0.05,
            session_id=session,
        )
        self.assertTrue(result.ok, f"{result.failure_class}: {result.failure_detail}")
        self.assertEqual(result.session_id, session)
        self.assertEqual(result.served_model, "claude-haiku-5-5")
        self.assertTrue(result.result_uuid)
        for value in (result.input_tokens, result.output_tokens, result.latency_ms):
            self.assertIsInstance(value, int)
            self.assertGreater(value, 0)
        self.assertGreater(result.cost_usd, 0)
        print(f"\nLIVE: session={result.session_id} uuid={result.result_uuid} model={result.served_model} "
              f"usage={result.usage} cost_usd={result.cost_usd}")


if __name__ == "__main__":
    unittest.main()
