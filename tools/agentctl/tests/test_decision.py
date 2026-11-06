"""Focused behavioral checks for the ADR-0005 acceptance journal."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agentctl.commands.decision import (
    _accepted_documents,
    _journal_path,
    _recover_journal,
    _write_journal,
)
from agentctl.hashing import sha256_hex


class DecisionJournalTests(unittest.TestCase):
    def test_recovery_restores_exact_bytes_after_consumption(self) -> None:
        before = (
            "# ADR-0005 — test\n\n## Status\n\nstatus: proposed\n\n"
            "## Required approvals\n\nHuman approval, as an invariant-adjacent security decision. Recorded at G0.\n"
        ).encode()
        readme_before = b"| ADR-0005 | Symmetric approval seal at Level 1 | proposed |\n"
        approval_id = "approval-1"
        approval_path = "agent-system/work/P0/approvals/ADR-0005-acceptance.json"
        adr_after, _ = _accepted_documents(before.decode(), approval_path, approval_id)
        readme_after = readme_before.decode().replace("| proposed |", "| accepted |", 1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adr_path = root / "docs/decisions/ADR-0005-symmetric-approval-seal.md"
            readme_path = root / "docs/decisions/README.md"
            adr_path.parent.mkdir(parents=True, exist_ok=True)
            readme_path.parent.mkdir(parents=True, exist_ok=True)
            adr_path.write_bytes(before)
            readme_path.write_bytes(readme_before)
            _journal_path(root).parent.mkdir(parents=True, exist_ok=True)
            journal = {
                "adr": "ADR-0005",
                "approval_id": approval_id,
                "approval_relative": approval_path,
                "target_hash": sha256_hex(before),
                "scope": "scope",
                "adr_path": "docs/decisions/ADR-0005-symmetric-approval-seal.md",
                "readme_path": "docs/decisions/README.md",
                "adr_before_b64": __import__("base64").b64encode(before).decode(),
                "adr_after_b64": __import__("base64").b64encode(adr_after.encode()).decode(),
                "readme_before_b64": __import__("base64").b64encode(readme_before).decode(),
                "readme_after_b64": __import__("base64").b64encode(readme_after.encode()).decode(),
            }
            _write_journal(_journal_path(root), journal)
            manager = SimpleNamespace(
                store=SimpleNamespace(
                    rows=lambda *_: [{
                        "path": approval_path,
                        "payload_json": json.dumps({
                            "authorized_action": "consequential_adr_acceptance",
                            "target_type": "adr",
                            "target_id": "ADR-0005",
                            "target_hash": sha256_hex(before),
                            "scope": "scope",
                            "consumed_at": "now",
                        }),
                    }]
                )
            )
            _recover_journal(root, manager)
            self.assertEqual(adr_path.read_bytes(), adr_after.encode())
            self.assertEqual(readme_path.read_bytes(), readme_after.encode())
            self.assertFalse(_journal_path(root).exists())

    def test_acceptance_requires_typed_reference(self) -> None:
        with self.assertRaises(ValueError):
            _accepted_documents("# ADR-0005\nstatus: proposed\n", "approval.json", "id")
