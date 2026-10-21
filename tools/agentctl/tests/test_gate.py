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


def _build_g0_replay_fixture(
    temporary_root: pathlib.Path,
    *,
    criterion_command: str | None = None,
    commit_approval_initially: bool = True,
    consumed_alternate_approval: bool = False,
    commit_result_after_close: bool = True,
    legacy_event_without_approval_ids: bool = False,
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path, dict, EventStore, GateEvaluator]:
    root = temporary_root / "repository"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for relative in (
        "agent-system/policies/approval-policy.json",
        "agent-system/policies/approval-principals.json",
        "agent-system/schemas/human-approval.schema.json",
        "agent-system/schemas/event.schema.json",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_repo_root() / relative, target)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Gate Test"], check=True
    )
    source = root / "agent-system/work/P0/gates/G0-criteria.json"
    approval_path = root / "agent-system/work/P0/approvals/G0-phase-approval.json"
    approval_ref = approval_path.relative_to(root).as_posix()
    source.parent.mkdir(parents=True, exist_ok=True)
    approval_path.parent.mkdir(parents=True, exist_ok=True)
    criteria = {
        "artifact_type": "phase-gate",
        "gate_id": "G0",
        "phase_id": "P0",
        "criteria": [],
        "human_approval_refs": [approval_ref],
    }
    if criterion_command is not None:
        criteria["criteria"].append(
            {
                "criterion_id": "FRESH-CHECK",
                "statement": "A deterministic criterion is recomputed at the new HEAD",
                "evidence_type": "deterministic_check",
                "evidence_command": criterion_command,
                "expected_exit_code": 0,
            }
        )
    criteria["criteria"].append(
        {
            "criterion_id": "APPROVAL-1",
            "statement": "A human authorizes these exact criteria",
            "evidence_type": "human_approval",
            "evidence_ref": approval_ref,
        }
    )
    source.write_text(json.dumps(criteria), encoding="utf-8")
    key = b"g" * 32
    approval = create_approval(
        key=key,
        principal="himanshu_jain",
        machine_identity="owner-machine",
        action="phase_authorization",
        target_type="phase_gate",
        target_id="G0",
        target_hash=sha256_of_json(criteria),
        scope="Close G0 against its immutable criteria",
        reason="Exercise post-commit gate replay",
    )
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    alternate_approval = None
    if consumed_alternate_approval:
        alternate_approval = create_approval(
            key=key,
            principal="himanshu_jain",
            machine_identity="owner-machine",
            action="phase_authorization",
            target_type="phase_gate",
            target_id="G0",
            target_hash=sha256_of_json(criteria),
            scope="Close G0 against its immutable criteria",
            reason="Exercise alternate consumed approval",
        )
        approval_path.write_text(json.dumps(alternate_approval), encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(root), "add", "agent-system/policies", "agent-system/schemas", source.relative_to(root).as_posix()],
        check=True,
    )
    if commit_approval_initially or consumed_alternate_approval:
        subprocess.run(
            ["git", "-C", str(root), "add", approval_ref], check=True
        )
    subprocess.run(
        ["git", "-C", str(root), "commit", "-qm", "initial gate evidence"], check=True
    )
    store = EventStore(temporary_root / "runtime.sqlite3", repository_root=root)
    manager = ApprovalManager(store, key=key)
    manager.register(approval, path=approval_ref)
    if alternate_approval is not None:
        manager.register(alternate_approval, path=approval_ref)
        approval_path.write_text(json.dumps(approval), encoding="utf-8")
    evaluator = GateEvaluator(root, approval_key=key, store=store)
    if legacy_event_without_approval_ids:
        from agentctl import gate as gate_module

        make_event = gate_module.make_event

        def legacy_make_event(*args, **kwargs):
            kwargs["artifact_refs"] = kwargs["artifact_refs"][:1]
            return make_event(*args, **kwargs)

        with mock.patch("agentctl.gate.make_event", side_effect=legacy_make_event):
            result = evaluator.evaluate(source)
    else:
        result = evaluator.evaluate(source)
    if result["gate_result"] != "passed":
        raise AssertionError("synthetic initial G0 evaluation did not pass")
    result_path = root / "agent-system/work/P0/gates/G0-result.json"
    if alternate_approval is not None:
        approval_path.write_text(json.dumps(alternate_approval), encoding="utf-8")
        expectation = evaluator._approval_expectations(
            alternate_approval, gate_id="G0", criteria_path=source
        )
        manager.consume(alternate_approval["approval_id"], **{
            "expected_action": expectation["expected_action"],
            "expected_target_type": expectation["expected_target_type"],
            "expected_target_id": expectation["expected_target_id"],
            "expected_target_hash": expectation["expected_target_hash"],
            "expected_scope": expectation["expected_scope"],
        })
    original_result_bytes = result_path.read_bytes()
    if commit_result_after_close:
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "add",
                result_path.relative_to(root).as_posix(),
                *([] if commit_approval_initially or alternate_approval is not None else [approval_ref]),
            ],
            check=True,
        )
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "close G0"], check=True)
    return root, source, approval_path, approval, store, evaluator


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

    def test_post_commit_closure_evidence_replay_does_not_reconsume_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = pathlib.Path(temporary)
            root = temporary_root / "repository"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            for relative in (
                "agent-system/policies/approval-policy.json",
                "agent-system/policies/approval-principals.json",
                "agent-system/schemas/human-approval.schema.json",
                "agent-system/schemas/event.schema.json",
            ):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.root / relative, target)
            subprocess.run(
                ["git", "-C", str(root), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(root), "config", "user.name", "Gate Test"],
                check=True,
            )
            source = root / "agent-system/work/P0/gates/G0-criteria.json"
            approval_path = root / "agent-system/work/P0/approvals/G0-phase-approval.json"
            approval_ref = approval_path.relative_to(root).as_posix()
            source.parent.mkdir(parents=True, exist_ok=True)
            approval_path.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(
                json.dumps(
                    {
                        **self._criteria(
                            {
                                "criterion_id": "APPROVAL-1",
                                "statement": "A human authorizes these exact criteria",
                                "evidence_type": "human_approval",
                                "evidence_ref": approval_ref,
                            }
                        ),
                        "gate_id": "G0",
                        "human_approval_refs": [approval_ref],
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
                target_id="G0",
                target_hash=sha256_of_json(json.loads(source.read_text())),
                scope="Close G0 against its immutable criteria",
                reason="Exercise post-commit gate replay",
            )
            approval_path.write_text(json.dumps(approval), encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "agent-system"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "initial gate evidence"], check=True)

            store = EventStore(temporary_root / "runtime.sqlite3", repository_root=root)
            ApprovalManager(store, key=key).register(approval, path=approval_ref)
            evaluator = GateEvaluator(root, approval_key=key, store=store)
            first = evaluator.evaluate(source)
            self.assertEqual(first["gate_result"], "passed")
            original_consumed_at = json.loads(
                store.rows(
                    "SELECT payload_json FROM artifacts WHERE artifact_id = ?",
                    (approval["approval_id"],),
                )[0]["payload_json"]
            )["consumed_at"]
            committed_result = root / "agent-system/work/P0/gates/G0-result.json"
            original_result_bytes = committed_result.read_bytes()
            subprocess.run(["git", "-C", str(root), "add", committed_result.relative_to(root).as_posix()], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "close G0"], check=True)

            replay = evaluator.evaluate(source)
            self.assertEqual(replay["gate_result"], "passed")
            self.assertEqual(replay["evaluated_against_commit"], subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
            ).strip())
            self.assertEqual(committed_result.read_bytes(), original_result_bytes)
            self.assertEqual(
                store.scalar(
                    "SELECT COUNT(*) FROM artifacts WHERE artifact_type = 'phase-gate'"
                ),
                1,
            )
            self.assertEqual(
                store.scalar(
                    "SELECT COUNT(*) FROM events WHERE event_type = 'phase_gate_evaluated'"
                ),
                1,
            )
            self.assertEqual(
                json.loads(
                    store.rows(
                        "SELECT payload_json FROM artifacts WHERE artifact_id = ?",
                        (approval["approval_id"],),
                    )[0]["payload_json"]
                )["consumed_at"],
                original_consumed_at,
            )

    def test_post_commit_replay_rejects_invalid_replay_proofs(self) -> None:
        scenarios = (
            "non_closure_change",
            "criteria_change",
            "approval_identity_change",
            "non_ancestor_prior_commit",
            "prior_non_passed_result",
            "fresh_criterion_failure",
        )
        for scenario in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temporary:
                command = (
                    'test "$(git log -1 --format=%s)" != "close G0"'
                    if scenario == "fresh_criterion_failure"
                    else None
                )
                root, source, approval_path, approval, store, evaluator = (
                    _build_g0_replay_fixture(pathlib.Path(temporary), criterion_command=command)
                )
                result_path = root / "agent-system/work/P0/gates/G0-result.json"
                original_result_bytes = result_path.read_bytes()

                if scenario == "non_closure_change":
                    (root / "README.md").write_text("unrelated source change\n", encoding="utf-8")
                    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
                    subprocess.run(["git", "-C", str(root), "commit", "-qm", "source change"], check=True)
                    with self.assertRaises(ApprovalRejected) as raised:
                        evaluator.evaluate(source)
                    self.assertEqual(raised.exception.code, "approval_replay_rejected")
                elif scenario == "criteria_change":
                    changed = json.loads(source.read_text(encoding="utf-8"))
                    changed["criteria"][0]["statement"] = "Changed criteria"
                    source.write_text(json.dumps(changed), encoding="utf-8")
                    with self.assertRaises(ApprovalRejected) as raised:
                        evaluator.evaluate(source)
                    self.assertEqual(raised.exception.code, "approval_replay_rejected")
                elif scenario == "approval_identity_change":
                    new_approval = create_approval(
                        key=b"g" * 32,
                        principal="himanshu_jain",
                        machine_identity="owner-machine",
                        action="phase_authorization",
                        target_type="phase_gate",
                        target_id="G0",
                        target_hash=sha256_of_json(json.loads(source.read_text())),
                        scope="Close G0 against its immutable criteria",
                        reason="Exercise changed approval identity",
                    )
                    approval_path.write_text(json.dumps(new_approval), encoding="utf-8")
                    ApprovalManager(store, key=b"g" * 32).register(
                        new_approval,
                        path=approval_path.relative_to(root).as_posix(),
                    )
                    with self.assertRaises(ApprovalRejected) as raised:
                        evaluator.evaluate(source)
                    self.assertEqual(raised.exception.code, "approval_replay_rejected")
                    stored = json.loads(
                        store.rows(
                            "SELECT payload_json FROM artifacts WHERE artifact_id = ?",
                            (new_approval["approval_id"],),
                        )[0]["payload_json"]
                    )
                    self.assertIsNone(stored["consumed_at"])
                elif scenario == "non_ancestor_prior_commit":
                    tree = subprocess.check_output(
                        ["git", "-C", str(root), "rev-parse", "HEAD^{tree}"], text=True
                    ).strip()
                    orphan_commit = subprocess.check_output(
                        ["git", "-C", str(root), "commit-tree", tree, "-m", "unrelated history"],
                        text=True,
                    ).strip()
                    row = store.rows(
                        "SELECT artifact_id, payload_json FROM artifacts "
                        "WHERE artifact_type = 'phase-gate'"
                    )[0]
                    payload = json.loads(row["payload_json"])
                    payload["evaluated_against_commit"] = orphan_commit
                    with store._connection() as connection:
                        connection.execute(
                            "UPDATE artifacts SET payload_json = ? WHERE artifact_id = ?",
                            (json.dumps(payload), row["artifact_id"]),
                        )
                    with self.assertRaises(ApprovalRejected) as raised:
                        evaluator.evaluate(source)
                    self.assertEqual(raised.exception.code, "approval_replay_rejected")
                elif scenario == "prior_non_passed_result":
                    row = store.rows(
                        "SELECT artifact_id, payload_json FROM artifacts "
                        "WHERE artifact_type = 'phase-gate'"
                    )[0]
                    payload = json.loads(row["payload_json"])
                    payload["gate_result"] = "failed"
                    with store._connection() as connection:
                        connection.execute(
                            "UPDATE artifacts SET payload_json = ? WHERE artifact_id = ?",
                            (json.dumps(payload), row["artifact_id"]),
                        )
                    with self.assertRaises(ApprovalRejected) as raised:
                        evaluator.evaluate(source)
                    self.assertEqual(raised.exception.code, "approval_consumed")
                else:
                    with self.assertRaises(ApprovalRejected) as raised:
                        evaluator.evaluate(source)
                    self.assertEqual(raised.exception.code, "approval_replay_rejected")

                self.assertEqual(result_path.read_bytes(), original_result_bytes)

    def test_replay_must_match_approval_ids_consumed_by_original_closure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, source, _, _, store, evaluator = _build_g0_replay_fixture(
                pathlib.Path(temporary), consumed_alternate_approval=True
            )
            with self.assertRaises(ApprovalRejected) as raised:
                evaluator.evaluate(source)
            self.assertEqual(raised.exception.code, "approval_replay_rejected")
            self.assertEqual(
                store.scalar(
                    "SELECT COUNT(*) FROM artifacts WHERE artifact_type = 'phase-gate'"
                ),
                1,
            )

    def test_replay_supports_approval_files_first_committed_with_closure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, source, _, _, store, evaluator = _build_g0_replay_fixture(
                pathlib.Path(temporary), commit_approval_initially=False
            )
            replay = evaluator.evaluate(source)
            self.assertEqual(replay["gate_result"], "passed")
            self.assertEqual(
                store.scalar(
                    "SELECT COUNT(*) FROM artifacts WHERE artifact_type = 'phase-gate'"
                ),
                1,
            )

    def test_filtered_criteria_cannot_satisfy_post_commit_replay(self) -> None:
        command = 'test "$(git log -1 --format=%s)" != "close G0"'
        with tempfile.TemporaryDirectory() as temporary:
            _, source, _, _, _, evaluator = _build_g0_replay_fixture(
                pathlib.Path(temporary), criterion_command=command
            )
            with self.assertRaises(ApprovalRejected) as raised:
                evaluator.evaluate(source, evidence_types=frozenset({"human_approval"}))
            self.assertEqual(raised.exception.code, "approval_replay_rejected")

    def test_replay_rechecks_changed_architecture_audit_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = pathlib.Path(temporary)
            root = temporary_root / "repository"
            (root / "agent-system/work/P0/gates").mkdir(parents=True)
            (root / "agent-system/work/P0/approvals").mkdir(parents=True)
            shutil.copytree(
                self.root / "agent-system/schemas", root / "agent-system/schemas"
            )
            shutil.copytree(
                self.root / "agent-system/policies", root / "agent-system/policies"
            )
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(
                ["git", "-C", str(root), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(root), "config", "user.name", "Gate Test"],
                check=True,
            )
            source = root / "agent-system/work/P0/gates/G0-criteria.json"
            audit_path = root / "agent-system/work/P0/gates/G0-architecture-audit.json"
            approval_path = root / "agent-system/work/P0/approvals/G0-phase-approval.json"
            audit_ref = audit_path.relative_to(root).as_posix()
            approval_ref = approval_path.relative_to(root).as_posix()
            criteria = {
                "artifact_type": "phase-gate",
                "gate_id": "G0",
                "phase_id": "P0",
                "criteria": [
                    {
                        "criterion_id": "AUDIT-1",
                        "statement": "The current architecture audit has no blockers",
                        "evidence_type": "artifact",
                        "evidence_ref": audit_ref,
                    },
                    {
                        "criterion_id": "APPROVAL-1",
                        "statement": "A human authorizes these exact criteria",
                        "evidence_type": "human_approval",
                        "evidence_ref": approval_ref,
                    },
                ],
                "human_approval_refs": [approval_ref],
            }
            source.write_text(json.dumps(criteria), encoding="utf-8")
            (root / "agent-system/CONSTRUCTION-LOG.md").write_text(
                "Synthetic audit fixture.\n", encoding="utf-8"
            )
            subprocess.run(["git", "-C", str(root), "add", "agent-system"], check=True)
            subprocess.run(
                ["git", "-C", str(root), "commit", "-qm", "criteria and schemas"],
                check=True,
            )
            audited_commit = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
            ).strip()
            audited_tree = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD^{tree}"], text=True
            ).strip()
            clean_audit = {
                "artifact_type": "architecture-audit",
                "audit_id": "AUDIT-REPLAY-IDEMPOTENCY",
                "gate_id": "G0",
                "pass_id": "merge",
                "auditor_role": "architectural_authority",
                "auditor_provider": "fixture-provider",
                "auditor_model": "fixture-model",
                "auditor_session_id": "fresh-session",
                "auditor_run_id": "fresh-run",
                "audited_commit": audited_commit,
                "audited_tree_hash": audited_tree,
                "artifact_families": ["all"],
                "decision_coverage": [
                    {
                        "decision_id": "G0",
                        "assessment": "implemented",
                        "evidence_ref": "agent-system/work/P0/gates/G0-criteria.json",
                    }
                ],
                "findings": [],
                "verdict": "no_blocking_findings",
                "independence_attestation": {"fresh_session": True, "distinct": True},
            }
            audit_path.write_text(json.dumps(clean_audit), encoding="utf-8")
            key = b"g" * 32
            approval = create_approval(
                key=key,
                principal="himanshu_jain",
                machine_identity="owner-machine",
                action="phase_authorization",
                target_type="phase_gate",
                target_id="G0",
                target_hash=sha256_of_json(criteria),
                scope="Close G0 against its immutable criteria",
                reason="Exercise architecture audit replay",
            )
            approval_path.write_text(json.dumps(approval), encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(root), "add", audit_ref, approval_ref], check=True
            )
            subprocess.run(
                ["git", "-C", str(root), "commit", "-qm", "audit and approval evidence"],
                check=True,
            )
            store = EventStore(temporary_root / "runtime.sqlite3", repository_root=root)
            ApprovalManager(store, key=key).register(approval, path=approval_ref)
            evaluator = GateEvaluator(root, approval_key=key, store=store)
            first = evaluator.evaluate(source)
            self.assertEqual(first["gate_result"], "passed")
            blocking_audit = json.loads(json.dumps(clean_audit))
            blocking_audit["findings"] = [
                {
                    "finding_id": "BLOCK-REPLAY",
                    "severity": "critical",
                    "violated_criterion": "G0",
                    "file": "agent-system/work/P0/gates/G0-criteria.json",
                    "location": "criteria[0]",
                    "reproducible_evidence": "seed",
                    "observed_behavior": "blocked",
                    "required_behavior": "clean",
                    "required_outcome": "repair",
                    "reviewer_confidence": "high",
                    "blocking": True,
                }
            ]
            blocking_audit["verdict"] = "blocking_findings"
            audit_path.write_text(json.dumps(blocking_audit), encoding="utf-8")
            result_path = root / "agent-system/work/P0/gates/G0-result.json"
            original_result_bytes = result_path.read_bytes()
            subprocess.run(
                ["git", "-C", str(root), "add", audit_ref, result_path.relative_to(root).as_posix()],
                check=True,
            )
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "close G0"], check=True)

            with self.assertRaises(ApprovalRejected) as raised:
                evaluator.evaluate(source)
            self.assertEqual(raised.exception.code, "approval_replay_rejected")
            self.assertEqual(result_path.read_bytes(), original_result_bytes)
            self.assertEqual(
                store.scalar(
                    "SELECT COUNT(*) FROM artifacts WHERE artifact_type = 'phase-gate'"
                ),
                1,
            )

    def test_any_prior_passed_g0_blocks_changed_criteria_closure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, source, _, _, store, evaluator = _build_g0_replay_fixture(
                pathlib.Path(temporary)
            )
            result_path = root / "agent-system/work/P0/gates/G0-result.json"
            original_result_bytes = result_path.read_bytes()
            changed = json.loads(source.read_text(encoding="utf-8"))
            changed["criteria"] = [
                {
                    "criterion_id": "CHANGED-PASSING-CHECK",
                    "statement": "A passing deterministic check cannot re-close G0",
                    "evidence_type": "deterministic_check",
                    "evidence_command": "true",
                    "expected_exit_code": 0,
                }
            ]
            changed["human_approval_refs"] = []
            source.write_text(json.dumps(changed), encoding="utf-8")

            with self.assertRaises(ApprovalRejected) as raised:
                evaluator.evaluate(source)
            self.assertEqual(raised.exception.code, "approval_replay_rejected")
            self.assertEqual(result_path.read_bytes(), original_result_bytes)
            self.assertEqual(
                store.scalar(
                    "SELECT COUNT(*) FROM artifacts WHERE artifact_type = 'phase-gate'"
                ),
                1,
            )
            self.assertEqual(
                store.scalar(
                    "SELECT COUNT(*) FROM events WHERE event_type = 'phase_gate_evaluated'"
                ),
                1,
            )

    def test_legacy_same_head_closure_is_idempotent_after_event_schema_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, source, _, approval, store, evaluator = _build_g0_replay_fixture(
                pathlib.Path(temporary),
                commit_result_after_close=False,
                legacy_event_without_approval_ids=True,
            )
            event_row = store.rows(
                "SELECT payload_json FROM events "
                "WHERE event_type = 'phase_gate_evaluated'"
            )[0]
            legacy_event = json.loads(event_row["payload_json"])
            self.assertEqual(len(legacy_event["artifact_refs"]), 1)
            result_path = root / "agent-system/work/P0/gates/G0-result.json"
            original_result_bytes = result_path.read_bytes()
            approval_before = json.loads(
                store.rows(
                    "SELECT payload_json FROM artifacts WHERE artifact_id = ?",
                    (approval["approval_id"],),
                )[0]["payload_json"]
            )

            result = evaluator.evaluate(source)

            self.assertEqual(result["gate_result"], "passed")
            self.assertEqual(result_path.read_bytes(), original_result_bytes)
            self.assertEqual(
                store.scalar(
                    "SELECT COUNT(*) FROM artifacts WHERE artifact_type = 'phase-gate'"
                ),
                1,
            )
            self.assertEqual(
                store.scalar(
                    "SELECT COUNT(*) FROM events WHERE event_type = 'phase_gate_evaluated'"
                ),
                1,
            )
            approval_after = json.loads(
                store.rows(
                    "SELECT payload_json FROM artifacts WHERE artifact_id = ?",
                    (approval["approval_id"],),
                )[0]["payload_json"]
            )
            self.assertEqual(approval_after["consumed_at"], approval_before["consumed_at"])

    def test_legacy_descendant_replay_without_approval_ids_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, source, _, _, store, evaluator = _build_g0_replay_fixture(
                pathlib.Path(temporary), legacy_event_without_approval_ids=True
            )
            result_path = root / "agent-system/work/P0/gates/G0-result.json"
            original_result_bytes = result_path.read_bytes()
            with self.assertRaises(ApprovalRejected) as raised:
                evaluator.evaluate(source)
            self.assertEqual(raised.exception.code, "approval_replay_rejected")
            self.assertEqual(result_path.read_bytes(), original_result_bytes)
            self.assertEqual(
                store.scalar(
                    "SELECT COUNT(*) FROM artifacts WHERE artifact_type = 'phase-gate'"
                ),
                1,
            )
            self.assertEqual(
                store.scalar(
                    "SELECT COUNT(*) FROM events WHERE event_type = 'phase_gate_evaluated'"
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
