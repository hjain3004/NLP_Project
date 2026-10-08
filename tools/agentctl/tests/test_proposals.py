"""Non-authoritative governance proposals are exact and cannot become live policy."""

from __future__ import annotations

import pathlib
import subprocess
import unittest

from agentctl.proposals import validate_governance_change_set, validate_g0_criteria_candidate


def _repo_root() -> pathlib.Path:
    return pathlib.Path(
        subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
    )


class ProposalValidationTests(unittest.TestCase):
    root = _repo_root()

    def test_governance_change_set_is_non_authoritative_and_binds_current_bytes(self) -> None:
        result = validate_governance_change_set(
            self.root / "agent-system/work/P0/proposals/governance-change-set.json",
            repository_root=self.root,
        )
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["authoritative"])

    def test_g0_candidate_has_corrected_bindings_without_replacing_canonical(self) -> None:
        result = validate_g0_criteria_candidate(
            self.root / "agent-system/work/P0/proposals/G0-criteria-candidate.json",
            repository_root=self.root,
        )
        self.assertEqual(result["status"], "ok")
        self.assertNotEqual(
            (self.root / "agent-system/work/P0/gates/G0-criteria.json").read_bytes(),
            (self.root / "agent-system/work/P0/proposals/G0-criteria-candidate.json").read_bytes(),
        )


if __name__ == "__main__":
    unittest.main()
