"""Generated program docs and repository documentation invariants."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from agentctl.docscheck import render_phase_gates, verify_docs
from agentctl.errors import VerificationFailure


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

    def test_unclassified_root_runbook_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clone = pathlib.Path(temporary) / "repository"
            subprocess.run(
                ["git", "clone", "--quiet", str(self.root), str(clone)],
                check=True,
            )
            for relative in (
                "RUNBOOK-tasks-1-2.md",
                "docs/archive/README.md",
                "docs/DOCUMENT_AUTHORITY.md",
                "agent-system/policies/document-authority.json",
            ):
                shutil.copy2(self.root / relative, clone / relative)
            subprocess.run(["git", "add", "--all"], cwd=clone, check=True)
            (clone / "UNMAPPED-RUNBOOK.md").write_text(
                "# Unmapped runbook\n\n```bash\necho execute-me\n```\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "add", "UNMAPPED-RUNBOOK.md"], cwd=clone, check=True
            )
            with self.assertRaises(VerificationFailure) as caught:
                verify_docs(clone)
            self.assertIn("UNMAPPED-RUNBOOK.md", str(caught.exception))

    def test_provider_adapters_do_not_duplicate_registry_bindings(self) -> None:
        registry = json.loads(
            (self.root / "agent-system/routing/capability-registry.json").read_text()
        )
        adapters = "\n".join(
            (self.root / name).read_text() for name in ("CLAUDE.md", "GEMINI.md")
        )
        for entry in registry["entries"]:
            self.assertNotIn(entry["capability_class"], adapters)

    def test_operational_roles_are_not_labelled_capability_classes(self) -> None:
        for name in ("verifier.md", "integrator.md"):
            text = (self.root / "agent-system/roles" / name).read_text()
            self.assertIn("**Operational role:**", text)
            self.assertNotIn("**Capability class:**", text)

    def test_challenger_scope_uses_task_contract_risk_vocabulary(self) -> None:
        challenger = json.loads(
            (self.root / "agent-system/routing/champion-challenger.json").read_text()
        )
        schema = json.loads(
            (self.root / "agent-system/schemas/task-contract.schema.json").read_text()
        )
        risks = set(schema["properties"]["routing"]["properties"]["risk"]["enum"])
        self.assertLessEqual(set(challenger["sample_scope"]), risks)

    def test_invariant_statement_tampering_is_rejected_even_when_ids_match(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clone = pathlib.Path(temporary) / "repository"
            subprocess.run(
                ["git", "clone", "--quiet", str(self.root), str(clone)], check=True
            )
            for relative in (
                "RUNBOOK-tasks-1-2.md",
                "docs/archive/README.md",
                "docs/DOCUMENT_AUTHORITY.md",
                "agent-system/policies/document-authority.json",
            ):
                shutil.copy2(self.root / relative, clone / relative)
            invariants = clone / "docs/security/INVARIANTS.md"
            invariants.write_text(
                invariants.read_text().replace(
                    "Currency and rounding policies are explicit",
                    "Currency and rounding policies may be implicit",
                    1,
                ),
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "--all"], cwd=clone, check=True)
            with self.assertRaises(VerificationFailure) as caught:
                verify_docs(clone)
            self.assertIn("statements", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
