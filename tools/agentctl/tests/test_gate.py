"""Phase-gate re-execution and architecture-audit independence tests."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from agentctl.approval import ApprovalManager, ApprovalRejected, create_approval
from agentctl.errors import PermissionViolation
from agentctl.gate import GateEvaluator, ingest_audit
from agentctl.hashing import sha256_hex, sha256_of_file
from agentctl.store import EventStore


def _repo_root() -> pathlib.Path:
    return pathlib.Path(
        subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
    )


class GateTests(unittest.TestCase):
    def _criteria(self, criterion: dict) -> dict:
        return {
            "artifact_type": "phase-gate",
            "gate_id": "TEST-GATE",
            "phase_id": "P0",
            "criteria": [criterion],
        }

    def test_open_manual_criterion_keeps_gate_open(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            source = root / "TEST-GATE-criteria.json"
            source.write_text(
                json.dumps(
                    self._criteria(
                        {
                            "criterion_id": "OPEN-1",
                            "statement": "Needs a bound manual attestation",
                            "evidence_type": "manual_attestation",
                            "result": "pass",
                        }
                    )
                ),
                encoding="utf-8",
            )
            result = GateEvaluator(root).evaluate(source)
            self.assertEqual(result["gate_result"], "open")
            self.assertEqual(result["criteria"][0]["result"], "open")

    def test_claimed_pass_is_discarded_and_failing_command_is_reexecuted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            source = root / "TEST-GATE-criteria.json"
            source.write_text(
                json.dumps(
                    self._criteria(
                        {
                            "criterion_id": "FAIL-1",
                            "statement": "The command must really pass",
                            "evidence_type": "deterministic_check",
                            "evidence_command": "python3 -c 'raise SystemExit(1)'",
                            "expected_exit_code": 0,
                            "result": "pass",
                        }
                    )
                ),
                encoding="utf-8",
            )
            result = GateEvaluator(root).evaluate(source)
            self.assertEqual(result["gate_result"], "failed")
            self.assertEqual(result["criteria"][0]["result"], "fail")

    def test_exit_code_and_output_hash_are_recomputed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            source = root / "TEST-GATE-criteria.json"
            source.write_text(
                json.dumps(
                    self._criteria(
                        {
                            "criterion_id": "PASS-1",
                            "statement": "The command and output are pinned",
                            "evidence_type": "deterministic_check",
                            "evidence_command": "python3 -c 'print(\"ok\")'",
                            "expected_exit_code": 0,
                            "evidence_hash": sha256_hex(b"ok\n"),
                            "result": "fail",
                        }
                    )
                ),
                encoding="utf-8",
            )
            result = GateEvaluator(root).evaluate(source)
            self.assertEqual(result["gate_result"], "passed")
            self.assertEqual(result["criteria"][0]["result"], "pass")
            written = json.loads((root / "TEST-GATE-result.json").read_text())
            self.assertEqual(written["gate_result"], "passed")
            original = json.loads(source.read_text())
            self.assertEqual(original["criteria"][0]["result"], "fail")

    def test_gate_binds_and_atomically_consumes_phase_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            source = directory / "TEST-GATE-criteria.json"
            approval_path = directory / "phase-approval.json"
            source.write_text(
                json.dumps(
                    {
                        **self._criteria(
                            {
                                "criterion_id": "APPROVAL-1",
                                "statement": "A human authorizes these exact criteria",
                                "evidence_type": "human_approval",
                                "evidence_ref": approval_path.name,
                            }
                        ),
                        "human_approval_refs": [approval_path.name],
                    }
                ),
                encoding="utf-8",
            )
            key = b"g" * 32
            approval = create_approval(
                key=key,
                principal="owner",
                machine_identity="owner-machine",
                action="phase_authorization",
                target_type="phase_gate",
                target_id="TEST-GATE",
                target_hash=sha256_of_file(source),
                scope="Close TEST-GATE against its immutable criteria",
                reason="Exercise gate approval consumption",
            )
            approval_path.write_text(json.dumps(approval), encoding="utf-8")
            store = EventStore(
                directory / "runtime.sqlite3", repository_root=_repo_root()
            )
            ApprovalManager(store, key=key).register(approval, path=approval_path.name)

            evaluator = GateEvaluator(directory, approval_key=key, store=store)
            result = evaluator.evaluate(source)
            self.assertEqual(result["gate_result"], "passed")
            stored = json.loads(
                store.rows(
                    "SELECT payload_json FROM artifacts WHERE artifact_id = ?",
                    (approval["approval_id"],),
                )[0]["payload_json"]
            )
            self.assertIsNotNone(stored["consumed_at"])
            with self.assertRaises(ApprovalRejected) as raised:
                evaluator.evaluate(source)
            self.assertEqual(raised.exception.code, "approval_consumed")

    def test_repository_root_check_accepts_a_logical_symlink_pwd(self) -> None:
        repository = _repo_root()
        g0 = json.loads(
            (repository / "agent-system/work/P0/gates/G0-criteria.json").read_text()
        )
        root_check = next(
            criterion
            for criterion in g0["criteria"]
            if criterion["criterion_id"] == "G0-01"
        )
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            physical = directory / "physical-repository"
            physical.mkdir()
            subprocess.run(["git", "init", "-q", str(physical)], check=True)
            logical = directory / "logical-repository"
            logical.symlink_to(physical, target_is_directory=True)
            source = physical / "TEST-GATE-criteria.json"
            source.write_text(json.dumps(self._criteria(root_check)), encoding="utf-8")
            with mock.patch.dict(os.environ, {"PWD": str(logical)}):
                result = GateEvaluator(physical).evaluate(source, write_result=False)
        self.assertEqual(result["criteria"][0]["result"], "pass")


class AuditTests(unittest.TestCase):
    root = _repo_root()

    def _audit(self, session: str = "fresh-session", run: str = "fresh-run") -> dict:
        return {
            "artifact_type": "architecture-audit",
            "audit_id": "AUDIT-1",
            "gate_id": "G0",
            "pass_id": "pass-1",
            "auditor_role": "architectural_authority",
            "auditor_provider": "fixture-provider",
            "auditor_model": "fixture-model",
            "auditor_session_id": session,
            "auditor_run_id": run,
            "audited_commit": "1" * 40,
            "audited_tree_hash": "2" * 40,
            "artifact_families": ["control-plane"],
            "decision_coverage": [],
            "findings": [],
            "verdict": "no_blocking_findings",
            "independence_attestation": {"fresh_session": True, "distinct": True},
        }

    def test_construction_author_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            log = directory / "CONSTRUCTION-LOG.md"
            log.write_text("author_session_id: authored-session\nauthor_run_id: authored-run\n")
            with self.assertRaises(PermissionViolation):
                ingest_audit("G0", self._audit(session="authored-session"), construction_log=log)

    def test_fresh_auditor_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            log = directory / "CONSTRUCTION-LOG.md"
            log.write_text("author_session_id: authored-session\n")
            store = EventStore(directory / "runtime.sqlite3", repository_root=self.root)
            result = ingest_audit("G0", self._audit(), construction_log=log, store=store)
            self.assertEqual(result["audit_id"], "AUDIT-1")
            self.assertEqual(store.scalar("SELECT COUNT(*) FROM artifacts"), 1)


if __name__ == "__main__":
    unittest.main()
