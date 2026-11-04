"""Regression tests for the final architecture audit's three verification gaps.

F3  A contract with no deterministic checks must not verify (vacuous verification).
F1a ``agentctl verify`` must drop credential-like variables from its own environment,
    exactly as ``agentctl integrate`` does.
F1b Gates run unsandboxed at the control-plane uid (AGENTS.md section 7: prohibited for
    high-risk tasks), so verify and integrate must refuse ``high`` and ``critical`` tasks.

Everything here uses real Git, the real event store, and the real scheduler. Where state is
corrupted outside the public API (``Lab.tamper``) the test says SIMULATED: it stands in for
stored state that an older build could have written (a vacuous pass, a high-risk contract
that was verified before the refusal existed).
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from unittest import mock

from agentctl.errors import PermissionViolation
from agentctl.integrate import integrate

from tests.helpers_lane_b import AGENTCTL_DIR, BRANCH, Lab

FILES = {"docs/hardening.txt": "synthetic\n"}


class _LabCase(unittest.TestCase):
    def setUp(self) -> None:
        self.lab = Lab()
        self.addCleanup(self.lab.close)
        self.scratch = pathlib.Path(tempfile.mkdtemp(prefix="verify-hardening-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.scratch, ignore_errors=True))

    def unverified_candidate(self, task_id: str, *, checks: tuple[str, ...], risk: str = "low"):
        """A real candidate left in state ``candidate``: registered at ``risk``, verification not yet run.

        Registration, dispatch, the Git commit, manifest ingest and the candidate binding are the
        real public paths; only the automatic ``verify_candidate`` call inside ``Lab.candidate`` is
        suppressed so the test can invoke verification itself.
        """
        register = self.lab.scheduler.register_task
        with mock.patch.object(
            self.lab.scheduler, "register_task",
            side_effect=lambda spec: register(replace(spec, risk=risk)),
        ), mock.patch.object(self.lab.scheduler, "verify_candidate"):
            candidate = self.lab.candidate(task_id, FILES, checks=checks, review=False)
        self.assertEqual(self.lab.state(task_id), "candidate")
        return candidate

    def evidence(self, task_id: str) -> dict:
        (row,) = self.lab.artifacts("verification-evidence", task_id)
        return row["payload"]


class NoDeterministicChecksTests(_LabCase):
    def test_verification_of_a_contract_with_no_checks_fails_with_a_named_reason(self) -> None:
        """F3: `all([])` made an empty check list a pass; nothing had been verified."""
        self.unverified_candidate("T-NC", checks=())

        result = self.lab.scheduler.verify_candidate("T-NC")

        self.assertEqual(result["state"], "repair_required")
        evidence = self.evidence("T-NC")
        self.assertIs(evidence["passed"], False)
        self.assertEqual(evidence["checks"], [])
        self.assertEqual(evidence["failure_reason"], "no_deterministic_checks")
        (event,) = self.lab.events("implementation_defect", "T-NC")
        self.assertEqual(event["reason_code"], "no_deterministic_checks")
        (row,) = self.lab.artifacts("verification-evidence", "T-NC")
        self.assertEqual(row["integrable"], 0)

    def test_integration_rejects_a_task_whose_contract_has_no_checks_and_moves_nothing(self) -> None:
        """F3: SIMULATED legacy state -- evidence stored as a pass by a build that verified vacuously.

        Integration must not rely on the stored `passed` flag when the contract owns no check.
        """
        self.lab.candidate("T-NC2", FILES, checks=("true",))
        self.assertEqual(self.lab.state("T-NC2"), "integration_ready")
        self.lab.tamper(
            "UPDATE tasks SET payload_json = json_set(payload_json, '$.deterministic_checks', json('[]')) "
            "WHERE task_id = ?", ("T-NC2",),
        )

        result = integrate("T-NC2", store=self.lab.store)

        self.assertEqual(result["outcome"], "rejected", result)
        self.assertEqual(result["reason_code"], "no_deterministic_checks", result)
        self.assertIsNone(self.lab.branch_head(BRANCH))
        self.assertEqual(self.lab.state("T-NC2"), "integration_ready")
        self.assertEqual(self.lab.artifacts("integration-record", "T-NC2"), [])


class HighRiskRefusalTests(_LabCase):
    def test_verify_refuses_high_and_critical_tasks_before_any_transition_or_check(self) -> None:
        """F1b: unsandboxed gates are prohibited for high-risk tasks; refuse before anything runs."""
        for index, risk in enumerate(("high", "critical")):
            with self.subTest(risk=risk):
                task_id = f"T-HR{index}"
                marker = self.scratch / f"gate-ran-{risk}"
                self.unverified_candidate(task_id, checks=(f"touch {marker}",), risk=risk)
                events_before = len(self.lab.events(task_id=task_id))

                with self.assertRaises(PermissionViolation) as raised:
                    self.lab.scheduler.verify_candidate(task_id)

                self.assertEqual(raised.exception.context["reason_code"], "unsandboxed_gate_high_risk")
                self.assertEqual(self.lab.state(task_id), "candidate")
                self.assertEqual(len(self.lab.events(task_id=task_id)), events_before)
                self.assertEqual(self.lab.artifacts("verification-evidence", task_id), [])
                self.assertFalse(marker.exists(), "a gate ran for a high-risk task")

    def test_verify_still_verifies_a_medium_risk_task(self) -> None:
        """F1b boundary: only high and critical are refused."""
        self.unverified_candidate("T-MR", checks=("true",), risk="medium")

        result = self.lab.scheduler.verify_candidate("T-MR")

        self.assertEqual(result["state"], "independent_review")
        self.assertIs(self.evidence("T-MR")["passed"], True)

    def test_integration_rejects_a_high_risk_task_without_running_gates_or_moving_the_branch(self) -> None:
        """F1b: SIMULATED -- the contract risk is raised after verification, as if a build without
        the verify refusal had verified it. Integration must still not run its gates."""
        marker = self.scratch / "integration-gate-ran"
        self.lab.candidate(
            "T-HRI", FILES, checks=(f'case "$PWD" in *agentctl-integration-gate-*) touch {marker};; esac; true',)
        )
        self.assertEqual(self.lab.state("T-HRI"), "integration_ready")
        self.lab.tamper(
            "UPDATE tasks SET payload_json = json_set(payload_json, '$.risk', 'high') WHERE task_id = ?",
            ("T-HRI",),
        )

        result = integrate("T-HRI", store=self.lab.store)

        self.assertEqual(result["outcome"], "rejected", result)
        self.assertEqual(result["reason_code"], "unsandboxed_gate_high_risk", result)
        self.assertIsNone(self.lab.branch_head(BRANCH))
        self.assertEqual(self.lab.state("T-HRI"), "integration_ready")
        self.assertFalse(marker.exists(), "an integration gate ran for a high-risk task")


class VerifyCommandEnvironmentTests(_LabCase):
    # Gates never inherit os.environ (they get an allow-list), so a probe inside a check cannot tell
    # whether the verify process dropped its credentials. The observable is the verify process's own
    # os.environ at the moment the real command hands off to verification.
    PROBE = (
        "import json, os, sys\n"
        "import agentctl.verify as verify_module\n"
        "real = verify_module.verify_task\n"
        "def probe(*args, **kwargs):\n"
        "    with open(os.environ['VERIFY_PROBE_OUT'], 'w') as handle:\n"
        "        json.dump({name: name in os.environ for name in sys.argv[2:]}, handle)\n"
        "    return real(*args, **kwargs)\n"
        "verify_module.verify_task = probe\n"
        "from agentctl.cli import main\n"
        "sys.exit(main(['verify', sys.argv[1]]))\n"
    )
    SENTINELS = ("ANTHROPIC_API_KEY", "SYNTHETIC_AUTH_TOKEN", "CLAUDE_SYNTHETIC_SESSION")
    KEPT = "AGENTCTL_SYNTHETIC_KEEP"

    def test_verify_command_drops_credential_like_variables_from_its_environment(self) -> None:
        """F1a: only `integrate` dropped credentials; `verify` kept them in the process that runs gates."""
        self.unverified_candidate("T-ENV", checks=("true",))
        out = self.scratch / "probe.json"
        env = {
            **os.environ,
            "PYTHONPATH": str(AGENTCTL_DIR),
            "VERIFY_PROBE_OUT": str(out),
            self.KEPT: "present",
            **{name: "synthetic-not-a-secret" for name in self.SENTINELS},
        }

        done = subprocess.run(
            [sys.executable, "-c", self.PROBE, "T-ENV", *self.SENTINELS, self.KEPT],
            cwd=self.lab.root, env=env, capture_output=True, text=True, timeout=120,
        )

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["state"], "independent_review")
        seen = json.loads(out.read_text())
        self.assertEqual({name: seen[name] for name in self.SENTINELS}, {name: False for name in self.SENTINELS})
        self.assertIs(seen[self.KEPT], True, "only credential-like variables may be dropped")


if __name__ == "__main__":
    unittest.main()
