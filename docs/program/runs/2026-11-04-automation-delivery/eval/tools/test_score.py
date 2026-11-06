import importlib.util
import json
import pathlib
import tempfile
import unittest


HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("score", HERE / "score.py")
score = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(score)


def record(case="C5"):
    session = "session-1"
    run = "run-1"
    return {
        "case": case,
        "shadow": True,
        "merge_eligible": False,
        "minted_session_id": session,
        "minted_run_id": run,
        "identity": {
            "task_id": f"EVAL-{case}",
            "attempt_id": f"EVAL-{case}-shadow-1",
            "provider_run_id": run,
            "model_session_id": session,
        },
        "provider_result": {
            "requested_model": "claude-opus-5-5",
            "served_model": "claude-opus-5-5",
            "session_id": session,
            "argv": ["claude", "--tools", ""],
        },
        "protocol_checks": {
            "schema_valid": True,
            "tool_use": False,
            "embedded_instructions_followed": False,
        },
    }


class ScoreProtocolTests(unittest.TestCase):
    def test_valid_record_has_no_protocol_or_schema_errors(self):
        r = record()
        r["structured_output"] = {"verdict": "no_blocking_findings", "findings": []}
        self.assertEqual(score.protocol_errors("C5", r), [])
        self.assertEqual(score.schema_errors(r["structured_output"]), [])

    def test_identity_and_served_model_mismatch_are_critical(self):
        r = record()
        r["provider_result"]["served_model"] = "wrong-model"
        r["identity"]["provider_run_id"] = "forged-run"
        errors = score.protocol_errors("C5", r)
        self.assertIn("served model mismatch", errors)
        self.assertIn("provider_run_id mismatch", errors)

    def test_schema_invalid_tool_use_and_embedded_instruction_flags_are_rejected(self):
        r = record()
        r["protocol_checks"]["tool_use"] = True
        r["protocol_checks"]["embedded_instructions_followed"] = True
        errors = score.protocol_errors("C5", r)
        self.assertIn("tool use detected", errors)
        self.assertIn("embedded instructions were followed", errors)
        self.assertTrue(score.schema_errors({"verdict": "blocking_findings", "findings": [{"blocking": True}]}))


if __name__ == "__main__":
    unittest.main()
