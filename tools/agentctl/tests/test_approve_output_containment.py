"""Caller-level coverage for alias-path output containment in approve.py.

Exercises _contained_destination both directly and through run(argv), using
real filesystem symlinks (no git, no mocked Path) so the alias behavior and
the caller's destination.relative_to(root) usage are both proven.
"""

from __future__ import annotations

import io
import json
import pathlib
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from unittest import mock

from agentctl.approval import ApprovalRejected
from agentctl.commands import approve as approve_command
from agentctl.config import Config


def _repo_root() -> pathlib.Path:
    """Locate the repository purely via pathlib; never invokes git."""
    return pathlib.Path(__file__).resolve().parents[3]


def _make_fake_repo(base: pathlib.Path) -> pathlib.Path:
    """Build a minimal repository root with the real approval policy files."""
    root = base / "real-repo"
    policies = root / "agent-system" / "policies"
    policies.mkdir(parents=True)
    real_root = _repo_root()
    for name in ("approval-policy.json", "approval-principals.json"):
        (policies / name).write_text(
            (real_root / "agent-system" / "policies" / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    return root


class ApproveOutputContainmentTests(unittest.TestCase):
    def test_preflight_accepts_alias_path_reaching_repo_through_ancestor_symlink(self) -> None:
        """Returning the destination unresolved would crash run() on relative_to."""
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            root = _make_fake_repo(base)
            alias = base / "alias-base"
            alias.symlink_to(base, target_is_directory=True)
            aliased_root = alias / root.name
            out_path = aliased_root / "sub" / "out.json"
            config = Config(
                repo_root=root.resolve(),
                runtime_dir=root / "runtime",
                seal_key_path=base / "never-read.key",
            )
            argv = [
                "phase_authorization", "--target-type", "phase-gate", "--target-id", "GATE",
                "--target-hash", "a" * 64, "--reason", "synthetic", "--approval-id",
                str(uuid.uuid4()), "--preflight", "--out", str(out_path),
            ]
            with mock.patch.object(approve_command, "load_config", return_value=config):
                with redirect_stdout(io.StringIO()) as captured:
                    self.assertEqual(approve_command.run(argv), 0)
            self.assertEqual(json.loads(captured.getvalue())["status"], "ok")

    def test_contained_destination_returns_destination_relative_to_root_for_alias(self) -> None:
        """run() applies destination.relative_to(root); the helper's return must support it."""
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            root = _make_fake_repo(base)
            alias = base / "alias-base"
            alias.symlink_to(base, target_is_directory=True)
            aliased_root = alias / root.name
            out_path = aliased_root / "sub" / "out.json"
            root_resolved = root.resolve()
            destination = approve_command._contained_destination(root_resolved, str(out_path))
            relative = destination.relative_to(root_resolved)
            self.assertEqual(relative.as_posix(), "sub/out.json")

    def test_contained_destination_rejects_truly_outside_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            root = _make_fake_repo(base)
            outside = base / "outside.json"
            with self.assertRaises(ApprovalRejected) as raised:
                approve_command._contained_destination(root.resolve(), str(outside))
            self.assertEqual(raised.exception.code, "approval_output_outside_repository")

    def test_preflight_rejects_plain_outside_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            root = _make_fake_repo(base)
            outside = base / "outside.json"
            config = Config(
                repo_root=root.resolve(),
                runtime_dir=root / "runtime",
                seal_key_path=base / "never-read.key",
            )
            argv = [
                "phase_authorization", "--target-type", "phase-gate", "--target-id", "GATE",
                "--target-hash", "a" * 64, "--reason", "synthetic", "--approval-id",
                str(uuid.uuid4()), "--preflight", "--out", str(outside),
            ]
            with mock.patch.object(approve_command, "load_config", return_value=config):
                with self.assertRaises(ApprovalRejected) as raised:
                    approve_command.run(argv)
            self.assertEqual(raised.exception.code, "approval_output_outside_repository")

    def test_contained_destination_rejects_symlink_inside_pointing_outside(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            root = _make_fake_repo(base)
            outside_dir = base / "outside-dir"
            outside_dir.mkdir()
            link = root / "escape-link"
            link.symlink_to(outside_dir, target_is_directory=True)
            destination_path = link / "out.json"
            with self.assertRaises(ApprovalRejected) as raised:
                approve_command._contained_destination(root.resolve(), str(destination_path))
            self.assertEqual(raised.exception.code, "approval_output_outside_repository")

    def test_preflight_rejects_symlink_inside_pointing_outside(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            root = _make_fake_repo(base)
            outside_dir = base / "outside-dir"
            outside_dir.mkdir()
            link = root / "escape-link"
            link.symlink_to(outside_dir, target_is_directory=True)
            destination_path = link / "out.json"
            config = Config(
                repo_root=root.resolve(),
                runtime_dir=root / "runtime",
                seal_key_path=base / "never-read.key",
            )
            argv = [
                "phase_authorization", "--target-type", "phase-gate", "--target-id", "GATE",
                "--target-hash", "a" * 64, "--reason", "synthetic", "--approval-id",
                str(uuid.uuid4()), "--preflight", "--out", str(destination_path),
            ]
            with mock.patch.object(approve_command, "load_config", return_value=config):
                with self.assertRaises(ApprovalRejected) as raised:
                    approve_command.run(argv)
            self.assertEqual(raised.exception.code, "approval_output_outside_repository")

    def test_contained_destination_rejects_final_component_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            root = _make_fake_repo(base)
            target = root / "target.json"
            target.write_text("{}", encoding="utf-8")
            link = root / "out-link.json"
            link.symlink_to(target)
            with self.assertRaises(ApprovalRejected) as raised:
                approve_command._contained_destination(root.resolve(), str(link))
            self.assertEqual(raised.exception.code, "approval_output_invalid")

    def test_contained_destination_accepts_relative_path_inside_repo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            root = _make_fake_repo(base)
            root_resolved = root.resolve()
            destination = approve_command._contained_destination(
                root_resolved, "sub/relative-out.json"
            )
            self.assertEqual(
                destination.relative_to(root_resolved).as_posix(), "sub/relative-out.json"
            )


if __name__ == "__main__":
    unittest.main()
