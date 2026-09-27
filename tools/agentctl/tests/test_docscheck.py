"""Generated program docs and repository documentation invariants."""

from __future__ import annotations

import pathlib
import subprocess
import unittest

from agentctl.docscheck import render_phase_gates, verify_docs


def _repo_root() -> pathlib.Path:
    return pathlib.Path(
        subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
    )


class DocumentationCheckTests(unittest.TestCase):
    root = _repo_root()

    def test_generated_gate_document_matches_machine_source(self) -> None:
        expected = (self.root / "docs/program/PHASE_GATES.md").read_text()
        self.assertEqual(render_phase_gates(self.root), expected)

    def test_current_repository_documentation_passes(self) -> None:
        result = verify_docs(self.root)
        self.assertEqual(result["status"], "ok")
        self.assertGreater(result["markdown_files_checked"], 10)


if __name__ == "__main__":
    unittest.main()
