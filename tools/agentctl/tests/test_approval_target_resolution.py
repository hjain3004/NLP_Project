"""Regression coverage for repository-owned approval target resolution."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone

from agentctl.approval import (
    SYNTHETIC_FIXTURE_KEY,
    ApprovalRejected,
    create_approval,
    resolve_approval_target,
)
from agentctl.hashing import sha256_of_json


def _repo_root() -> pathlib.Path:
    return pathlib.Path(
        subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
    )


class ApprovalTargetResolutionTests(unittest.TestCase):
    root = _repo_root()
    proposal_id = "P0-GOVERNANCE-CHANGESET-2026-10-07"

    def test_real_cli_preflight_resolves_governance_proposal_without_prompt(self) -> None:
        proposal_path = self.root / "agent-system/work/P0/proposals/governance-change-set.json"
        proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
        target_hash = sha256_of_json(proposal)
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(self.root / "tools/agentctl")
        result = subprocess.run(
            [
                "python3",
                "-m",
                "agentctl",
                "approve",
                "schema_commitment",
                "--target-type",
                "governance-change-set",
                "--target-id",
                self.proposal_id,
                "--target-hash",
                target_hash,
                "--scope",
                "Apply only the exact governance-change-set proposal operations",
                "--reason",
                "Synthetic exact governance target",
                "--preflight",
            ],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"status": "ok"', result.stdout)

    def test_synthetic_governance_approval_verifies_against_current_payload(self) -> None:
        proposal = json.loads(
            (self.root / "agent-system/work/P0/proposals/governance-change-set.json").read_text(
                encoding="utf-8"
            )
        )
        approval = create_approval(
            key=SYNTHETIC_FIXTURE_KEY,
            principal="synthetic-fixture-principal",
            machine_identity="synthetic-fixture-machine",
            action="schema_commitment",
            target_type="governance-change-set",
            target_id=self.proposal_id,
            target_hash=sha256_of_json(proposal),
            scope="Apply only the exact governance-change-set proposal operations",
            reason="Synthetic exact governance target",
            now=datetime(2026, 9, 26, tzinfo=timezone.utc),
            lifetime_seconds=3600,
        )
        from agentctl.approval import verify_approval

        verified = verify_approval(
            approval,
            SYNTHETIC_FIXTURE_KEY,
            expected_action="schema_commitment",
            expected_target_type="governance-change-set",
            expected_target_id=self.proposal_id,
            expected_target_hash=sha256_of_json(proposal),
            expected_scope=approval["scope"],
            now=datetime(2026, 9, 26, 0, 30, tzinfo=timezone.utc),
            policy={"tiers": {"schema_commitment": "interactive_tty_with_key_unlock"}},
            repository_root=self.root,
        )
        self.assertEqual(verified["target_id"], self.proposal_id)

    def test_resolver_rejects_unknown_wrong_and_missing_targets(self) -> None:
        with self.assertRaises(ApprovalRejected) as unknown:
            resolve_approval_target("unknown-target", "whatever", repository_root=self.root)
        self.assertEqual(unknown.exception.code, "approval_unknown_target")

        with self.assertRaises(ApprovalRejected) as wrong_id:
            resolve_approval_target(
                "governance-change-set", "WRONG-PROPOSAL", repository_root=self.root
            )
        self.assertEqual(wrong_id.exception.code, "approval_wrong_target")

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ApprovalRejected) as missing:
                resolve_approval_target(
                    "governance-change-set", self.proposal_id, repository_root=directory
                )
        self.assertEqual(missing.exception.code, "approval_target_unreadable")


if __name__ == "__main__":
    unittest.main()
