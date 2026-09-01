"""Artifact dataclasses and the normative task-state enumeration."""

from __future__ import annotations

import dataclasses
import json
import pathlib
import subprocess
import unittest

from agentctl.models import (
    EXCEPTIONAL_STATES,
    MODEL_BY_ARTIFACT_TYPE,
    TERMINAL_STATES,
    ArtifactType,
    TaskContract,
    TaskState,
    WorkPackage,
)

LINEAR_STATES = [
    "draft",
    "validated",
    "ready",
    "leased",
    "running",
    "candidate",
    "deterministic_verification",
    "independent_review",
    "integration_ready",
    "integrating",
    "completed",
]
EXCEPTIONAL = [
    "blocked",
    "repair_required",
    "replan_required",
    "escalated",
    "failed",
    "canceled",
    "stale",
]

_SYNTHETIC: dict[str, dict[str, object]] = {
    "work-package": {
        "artifact_type": "work-package",
        "work_package_id": "P0-C",
        "phase_id": "P0",
        "title": "t",
        "objective": "o",
        "status": "proposed",
        "initial_capability_class": "standard_implementation",
        "owning_planner_role": "planner",
        "bootstrap_phase": True,
        "created_at": "2026-09-01T00:00:00Z",
        "plan_hash": "0" * 64,
    },
    "task-contract": {
        "artifact_type": "task-contract",
        "task_id": "T-9",
        "work_package_id": "P0-C",
        "contract_version": "1",
        "contract_hash": "0" * 64,
        "base_commit": "unbound",
        "execution_mode": "bootstrap_retrospective",
        "status": "proposed",
        "created_at": "2026-09-01T00:00:00Z",
        "expires_at": "2026-09-02T00:00:00Z",
    },
    "context-manifest": {
        "artifact_type": "context-manifest",
        "context_hash": "0" * 64,
        "task_id": "T-9",
        "attempt_id": "A-1",
        "compiled_at": "2026-09-01T00:00:00Z",
        "role": "standard-implementer",
    },
    "implementation-manifest": {
        "artifact_type": "implementation-manifest",
        "task_id": "T-9",
        "attempt_id": "A-1",
        "lease_id": "L-1",
        "lease_epoch": 1,
        "fencing_token": 1,
        "worker_role": "standard-implementer",
        "provider": "manual",
        "model": "human",
        "contract_hash": "0" * 64,
        "context_hash": "0" * 64,
        "base_commit": "0" * 40,
        "result_commit": "1" * 40,
        "termination_reason": "completed",
    },
    "review-verdict": {
        "artifact_type": "review-verdict",
        "review_id": "R-1",
        "task_id": "T-9",
        "attempt_id": "A-1",
        "reviewer_role": "verifier",
        "reviewer_session_id": "S-2",
        "reviewer_run_id": "run-2",
        "reviewed_result_commit": "1" * 40,
        "contract_hash": "0" * 64,
        "context_hash": "0" * 64,
        "verdict": "no_blocking_findings",
    },
    "integration-manifest": {
        "artifact_type": "integration-manifest",
        "integration_id": "I-1",
        "task_id": "T-9",
        "integrator_role": "integrator",
        "integrator_session_id": "S-3",
        "candidate_result_commit": "1" * 40,
        "reviewed_diff_hash": "0" * 64,
        "review_id": "R-1",
        "integration_base_commit": "0" * 40,
        "integration_commit": "2" * 40,
        "merge_strategy": "conflict_free_preserving_candidate",
        "candidate_patch_unchanged": True,
        "completed_at": "2026-09-01T00:00:00Z",
    },
    "human-approval": {
        "artifact_type": "human-approval",
        "approval_id": "AP-1",
        "schema_version": "1",
        "approver_principal": "himanshu",
        "machine_identity": "laptop",
        "authentication_method": "interactive_tty_with_key_unlock",
        "authenticated_at": "2026-09-01T00:00:00Z",
        "authorized_action": "phase_gate_closure",
        "target_type": "phase-gate",
        "target_id": "G0",
        "target_hash": "0" * 64,
        "issued_at": "2026-09-01T00:00:00Z",
        "expires_at": "2026-09-02T00:00:00Z",
        "single_use": True,
        "nonce": "abc",
        "control_plane_seal": "deadbeef",
    },
    "phase-gate": {
        "artifact_type": "phase-gate",
        "gate_id": "G0",
        "phase_id": "P0",
        "evaluated_at": "2026-09-01T00:00:00Z",
        "evaluated_against_commit": "2" * 40,
        "gate_result": "open",
    },
    "event": {
        "artifact_type": "event",
        "event_id": "E-1",
        "trace_id": "TR-1",
        "occurred_at": "2026-09-01T00:00:00Z",
        "actor_role": "control-plane",
        "reason_code": "state_advanced",
        "event_type": "task_validated",
    },
    "architecture-audit": {
        "artifact_type": "architecture-audit",
        "audit_id": "AA-1",
        "gate_id": "G0",
        "pass_id": "pass-1",
        "auditor_role": "architect",
        "auditor_session_id": "S-4",
        "auditor_run_id": "run-4",
        "audited_commit": "2" * 40,
        "audited_tree_hash": "0" * 64,
        "verdict": "no_blocking_findings",
    },
}


class TaskStateTests(unittest.TestCase):
    def test_linear_and_exceptional_members_present(self) -> None:
        values = {s.value for s in TaskState}
        self.assertEqual(values, set(LINEAR_STATES) | set(EXCEPTIONAL))

    def test_terminal_states(self) -> None:
        self.assertEqual(
            {s.value for s in TERMINAL_STATES}, {"completed", "failed", "canceled"}
        )

    def test_exceptional_states(self) -> None:
        self.assertEqual({s.value for s in EXCEPTIONAL_STATES}, set(EXCEPTIONAL))

    def test_string_enum_compares_to_raw_value(self) -> None:
        self.assertEqual(TaskState.DRAFT, "draft")


class ArtifactModelTests(unittest.TestCase):
    def test_every_schema_stem_has_a_model(self) -> None:
        root = _repo_root() / "agent-system" / "schemas"
        stems = {p.name.replace(".schema.json", "") for p in root.glob("*.schema.json")}
        self.assertEqual(set(MODEL_BY_ARTIFACT_TYPE), stems)
        self.assertEqual(set(MODEL_BY_ARTIFACT_TYPE), {t.value for t in ArtifactType})

    def test_each_model_is_frozen_and_round_trips(self) -> None:
        for artifact_type, cls in MODEL_BY_ARTIFACT_TYPE.items():
            data = _SYNTHETIC[artifact_type]
            obj = cls.from_mapping(data)
            for field in dataclasses.fields(cls):
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    setattr(obj, field.name, "mutated")
            for field in dataclasses.fields(cls):
                self.assertEqual(getattr(obj, field.name), data[field.name])

    def test_from_mapping_rejects_wrong_artifact_type(self) -> None:
        data = dict(_SYNTHETIC["work-package"], artifact_type="task-contract")
        with self.assertRaises(ValueError):
            WorkPackage.from_mapping(data)

    def test_from_mapping_rejects_missing_field(self) -> None:
        data = {k: v for k, v in _SYNTHETIC["task-contract"].items() if k != "base_commit"}
        with self.assertRaises(ValueError):
            TaskContract.from_mapping(data)

    def test_synthetic_fixtures_are_json_serialisable(self) -> None:
        # Guards against a typo turning a fixture into something non-round-trippable.
        for data in _SYNTHETIC.values():
            json.loads(json.dumps(data))


def _repo_root() -> pathlib.Path:
    out = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
    return pathlib.Path(out)


if __name__ == "__main__":
    unittest.main()
