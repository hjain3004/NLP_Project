"""Phase-gate re-execution and architecture-audit independence tests."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from agentctl.approval import ApprovalManager, ApprovalRejected, create_approval
from agentctl.errors import InvalidArtifact, PermissionViolation
from agentctl.gate import GateEvaluator, ingest_audit
from agentctl.hashing import sha256_hex, sha256_of_file, sha256_of_json
from agentctl.store import EventStore


def _repo_root() -> pathlib.Path:
    return pathlib.Path(
        subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
    )


class GateTests(unittest.TestCase):
    root = _repo_root()

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
                principal="himanshu_jain",
                machine_identity="owner-machine",
                action="phase_authorization",
                target_type="phase_gate",
                target_id="TEST-GATE",
                target_hash=sha256_of_json(json.loads(source.read_text())),
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
            repeated = evaluator.evaluate(source)
            self.assertEqual(repeated["gate_result"], "passed")
            self.assertEqual(
                store.scalar(
                    "SELECT COUNT(*) FROM artifacts WHERE artifact_type = 'phase-gate'"
                ),
                1,
            )

    def test_gate_approval_consumption_and_authoritative_result_are_one_transaction(self) -> None:
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
                principal="himanshu_jain",
                machine_identity="owner-machine",
                action="phase_authorization",
                target_type="phase_gate",
                target_id="TEST-GATE",
                target_hash=sha256_of_json(json.loads(source.read_text())),
                scope="Close TEST-GATE against its immutable criteria",
                reason="Exercise atomic gate closure",
            )
            approval_path.write_text(json.dumps(approval), encoding="utf-8")
            store = EventStore(
                directory / "runtime.sqlite3", repository_root=_repo_root()
            )
            ApprovalManager(store, key=key).register(approval, path=approval_path.name)
            with store._connection() as connection:
                connection.execute(
                    "CREATE TRIGGER reject_gate_artifact BEFORE INSERT ON artifacts "
                    "WHEN NEW.artifact_type = 'phase-gate' BEGIN "
                    "SELECT RAISE(ABORT, 'injected gate persistence failure'); END"
                )

            with self.assertRaises(Exception):
                GateEvaluator(directory, approval_key=key, store=store).evaluate(source)
            stored = json.loads(
                store.rows(
                    "SELECT payload_json FROM artifacts WHERE artifact_id = ?",
                    (approval["approval_id"],),
                )[0]["payload_json"]
            )
            self.assertIsNone(stored["consumed_at"])
            self.assertEqual(
                store.scalar(
                    "SELECT COUNT(*) FROM artifacts WHERE artifact_type = 'phase-gate'"
                ),
                0,
            )

    def test_gate_result_view_is_recoverable_after_post_commit_write_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            source = directory / "TEST-GATE-criteria.json"
            source.write_text(
                json.dumps(
                    self._criteria(
                        {
                            "criterion_id": "PASS-1",
                            "statement": "Passes",
                            "evidence_type": "deterministic_check",
                            "evidence_command": "true",
                            "expected_exit_code": 0,
                        }
                    )
                ),
                encoding="utf-8",
            )
            store = EventStore(
                directory / "runtime.sqlite3", repository_root=_repo_root()
            )
            evaluator = GateEvaluator(directory, store=store)
            original_write = pathlib.Path.write_text
            calls = {"count": 0}

            def fail_once(path: pathlib.Path, *args: object, **kwargs: object) -> int:
                if path.name == "TEST-GATE-result.json" and calls["count"] == 0:
                    calls["count"] += 1
                    raise OSError("injected result-view failure")
                return original_write(path, *args, **kwargs)

            with mock.patch.object(pathlib.Path, "write_text", fail_once):
                with self.assertRaises(OSError):
                    evaluator.evaluate(source)
            self.assertEqual(
                store.scalar(
                    "SELECT COUNT(*) FROM artifacts WHERE artifact_type = 'phase-gate'"
                ),
                1,
            )
            result = evaluator.evaluate(source)
            self.assertEqual(result["gate_result"], "passed")
            self.assertTrue((directory / "TEST-GATE-result.json").is_file())
            self.assertEqual(
                store.scalar(
                    "SELECT COUNT(*) FROM artifacts WHERE artifact_type = 'phase-gate'"
                ),
                1,
            )

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

    def test_clean_checkout_criterion_ignores_dirty_worktree_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clone = pathlib.Path(temporary) / "repository"
            subprocess.run(
                ["git", "clone", "--quiet", "--shared", str(self.root), str(clone)],
                check=True,
            )
            agents = clone / "AGENTS.md"
            agents.write_text("dirty content that fails documentation checks\n", encoding="utf-8")
            criteria_path = clone / "agent-system/work/P0/gates/G0-criteria.json"
            source = json.loads(criteria_path.read_text(encoding="utf-8"))
            criterion = next(
                item for item in source["criteria"] if item["criterion_id"] == "G0-11"
            )
            result = GateEvaluator(clone)._evaluate_criterion(
                criterion, gate_id="G0", criteria_path=criteria_path
            )
        self.assertEqual(result["result"], "pass")

    def test_nested_git_criterion_rejects_file_markers_anywhere(self) -> None:
        g0 = json.loads(
            (self.root / "agent-system/work/P0/gates/G0-criteria.json").read_text()
        )
        criterion = next(
            item for item in g0["criteria"] if item["criterion_id"] == "G0-03"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary) / "repository"
            subprocess.run(
                ["git", "clone", "--quiet", "--shared", str(self.root), str(root)],
                check=True,
            )
            marker = root / "AppleBooks" / ".git"
            marker.write_text("gitdir: /tmp/forged-nested-repository\n", encoding="utf-8")
            criteria_path = root / "agent-system/work/P0/gates/G0-criteria.json"
            result = GateEvaluator(root)._evaluate_criterion(
                criterion, gate_id="G0", criteria_path=criteria_path
            )
        self.assertEqual(result["result"], "fail")

    def test_architecture_audit_gate_rejects_untrusted_or_blocking_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary) / "repository"
            (root / "agent-system/work/P0/gates").mkdir(parents=True)
            shutil.copytree(
                self.root / "agent-system/schemas", root / "agent-system/schemas"
            )
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(
                ["git", "config", "user.email", "fixture@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Fixture"], cwd=root, check=True
            )
            (root / "seed.txt").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "add", "--all"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=root, check=True)
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip()
            tree = subprocess.check_output(
                ["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True
            ).strip()
            valid = {
                "artifact_type": "architecture-audit",
                "audit_id": "AUDIT-GATE-1",
                "gate_id": "G0",
                "pass_id": "merge",
                "auditor_role": "architectural_authority",
                "auditor_provider": "fixture-provider",
                "auditor_model": "fixture-model",
                "auditor_session_id": "fresh-session",
                "auditor_run_id": "fresh-run",
                "audited_commit": commit,
                "audited_tree_hash": tree,
                "artifact_families": ["all"],
                "decision_coverage": [
                    {
                        "decision_id": "G0",
                        "assessment": "implemented",
                        "evidence_ref": "seed.txt",
                    }
                ],
                "findings": [],
                "verdict": "no_blocking_findings",
                "independence_attestation": {"fresh_session": True, "distinct": True},
            }
            audit_path = root / "agent-system/work/P0/gates/G0-architecture-audit.json"
            criteria_path = root / "G0-criteria.json"
            criteria_path.write_text(
                json.dumps(
                    {
                        **self._criteria(
                            {
                                "criterion_id": "AUDIT-1",
                                "statement": "Audit must be trusted and clean",
                                "evidence_type": "artifact",
                                "evidence_ref": audit_path.relative_to(root).as_posix(),
                            }
                        ),
                        "gate_id": "G0",
                    }
                ),
                encoding="utf-8",
            )
            cases = []
            incomplete = json.loads(json.dumps(valid))
            incomplete["artifact_families"] = []
            cases.append(incomplete)
            nonindependent = json.loads(json.dumps(valid))
            nonindependent["independence_attestation"]["distinct"] = False
            cases.append(nonindependent)
            stale = json.loads(json.dumps(valid))
            stale["audited_commit"] = "1" * 40
            cases.append(stale)
            blocking = json.loads(json.dumps(valid))
            blocking["findings"] = [
                {
                    "finding_id": "BLOCK-1",
                    "severity": "critical",
                    "violated_criterion": "G0",
                    "reproducible_evidence": "seed.txt",
                    "observed_behavior": "blocked",
                    "required_behavior": "clean",
                    "required_outcome": "repair",
                    "blocking": True,
                }
            ]
            blocking["verdict"] = "blocking_findings"
            cases.append(blocking)
            for index, audit in enumerate(cases):
                with self.subTest(index=index):
                    audit_path.write_text(json.dumps(audit), encoding="utf-8")
                    result = GateEvaluator(root).evaluate(criteria_path, write_result=False)
            self.assertEqual(result["criteria"][0]["result"], "fail")

    def test_repository_worktree_gitfile_is_not_a_nested_repository(self) -> None:
        completed = GateEvaluator(self.root)._nested_git_check()
        self.assertEqual(completed.returncode, 0, completed.stdout.decode())

    def test_architecture_audit_remains_current_after_evidence_only_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary) / "repository"
            audit_path = root / "agent-system/work/P0/gates/G0-architecture-audit.json"
            audit_path.parent.mkdir(parents=True)
            shutil.copytree(
                self.root / "agent-system/schemas", root / "agent-system/schemas"
            )
            criteria_path = root / "G0-criteria.json"
            criteria_path.write_text(
                json.dumps(
                    {
                        **self._criteria(
                        {
                            "criterion_id": "AUDIT-1",
                            "statement": "Audit must remain bound after evidence commit",
                            "evidence_type": "artifact",
                            "evidence_ref": audit_path.relative_to(root).as_posix(),
                        }
                        ),
                        "gate_id": "G0",
                    }
                ),
                encoding="utf-8",
            )
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(
                ["git", "config", "user.email", "fixture@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Fixture"], cwd=root, check=True
            )
            subprocess.run(["git", "add", "--all"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "audited base"], cwd=root, check=True)
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip()
            tree = subprocess.check_output(
                ["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True
            ).strip()
            audit_path.write_text(
                json.dumps(
                    {
                        "artifact_type": "architecture-audit",
                        "audit_id": "AUDIT-EVIDENCE-COMMIT",
                        "gate_id": "G0",
                        "pass_id": "merge",
                        "auditor_role": "architectural_authority",
                        "auditor_provider": "fixture-provider",
                        "auditor_model": "fixture-model",
                        "auditor_session_id": "fresh-session",
                        "auditor_run_id": "fresh-run",
                        "audited_commit": commit,
                        "audited_tree_hash": tree,
                        "artifact_families": ["all"],
                        "decision_coverage": [
                            {
                                "decision_id": "G0",
                                "assessment": "implemented",
                                "evidence_ref": "G0-criteria.json",
                            }
                        ],
                        "findings": [],
                        "verdict": "no_blocking_findings",
                        "independence_attestation": {
                            "fresh_session": True,
                            "distinct": True,
                        },
                    }
                ),
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "--all"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "record audit evidence"],
                cwd=root,
                check=True,
            )
            result = GateEvaluator(root).evaluate(criteria_path, write_result=False)
            self.assertEqual(result["criteria"][0]["result"], "pass")


class AuditTests(unittest.TestCase):
    root = _repo_root()

    @classmethod
    def _head(cls) -> tuple[str, str]:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=cls.root, text=True
        ).strip()
        tree = subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=cls.root, text=True
        ).strip()
        return commit, tree

    def _audit(self, session: str = "fresh-session", run: str = "fresh-run") -> dict:
        commit, tree = self._head()
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
            "audited_commit": commit,
            "audited_tree_hash": tree,
            "artifact_families": ["control-plane"],
            "decision_coverage": [
                {
                    "decision_id": "G0-01",
                    "assessment": "implemented",
                    "evidence_ref": "agent-system/work/P0/gates/G0-criteria.json",
                }
            ],
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

    def test_empty_audit_scope_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = pathlib.Path(temporary) / "CONSTRUCTION-LOG.md"
            log.write_text("author_session_id: authored-session\n")
            audit = self._audit()
            audit["artifact_families"] = []
            with self.assertRaises(InvalidArtifact):
                ingest_audit(
                    "G0", audit, construction_log=log, repository_root=self.root
                )

    def test_nonindependent_or_contradictory_audit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = pathlib.Path(temporary) / "CONSTRUCTION-LOG.md"
            log.write_text("author_session_id: authored-session\n")
            nonindependent = self._audit()
            nonindependent["independence_attestation"]["distinct"] = False
            with self.assertRaises(PermissionViolation):
                ingest_audit(
                    "G0", nonindependent, construction_log=log, repository_root=self.root
                )

            contradictory = self._audit()
            contradictory["findings"] = [{"blocking": True}]
            with self.assertRaises(InvalidArtifact):
                ingest_audit(
                    "G0", contradictory, construction_log=log, repository_root=self.root
                )

    def test_stale_audit_binding_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = pathlib.Path(temporary) / "CONSTRUCTION-LOG.md"
            log.write_text("author_session_id: authored-session\n")
            audit = self._audit()
            audit["audited_commit"] = "1" * 40
            with self.assertRaises(InvalidArtifact):
                ingest_audit(
                    "G0", audit, construction_log=log, repository_root=self.root
                )

    def test_fresh_auditor_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            log = directory / "CONSTRUCTION-LOG.md"
            log.write_text("author_session_id: authored-session\n")
            store = EventStore(directory / "runtime.sqlite3", repository_root=self.root)
            result = ingest_audit(
                "G0",
                self._audit(),
                construction_log=log,
                store=store,
                repository_root=self.root,
            )
            self.assertEqual(result["audit_id"], "AUDIT-1")
            self.assertEqual(store.scalar("SELECT COUNT(*) FROM artifacts"), 1)


if __name__ == "__main__":
    unittest.main()
