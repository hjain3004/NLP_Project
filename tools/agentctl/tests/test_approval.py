"""Hash-bound approval, atomic consumption, and key-doctor tests."""

from __future__ import annotations

import copy
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from agentctl.approval import (
    SYNTHETIC_FIXTURE_KEY,
    ApprovalManager,
    ApprovalRejected,
    doctor,
    seal_approval,
    verify_approval,
)
from agentctl.config import PermissionViolation
from agentctl.errors import ConfigError
from agentctl.store import EventStore


def _repo_root() -> pathlib.Path:
    return pathlib.Path(
        subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
    )


class ApprovalTests(unittest.TestCase):
    root = _repo_root()

    def setUp(self) -> None:
        self.fixture = json.loads(
            (self.root / "agent-system/examples/valid/08-human-approval.json").read_text(
                encoding="utf-8"
            )
        )

    def test_committed_valid_fixture_is_cryptographically_accepted(self) -> None:
        verified = verify_approval(
            self.fixture,
            SYNTHETIC_FIXTURE_KEY,
            expected_action="phase_authorization",
            expected_target_hash=self.fixture["target_hash"],
            expected_scope=self.fixture["scope"],
            now=datetime(2026, 9, 26, tzinfo=timezone.utc),
        )
        self.assertEqual(verified["approval_id"], "EX-APPROVAL-001")

    def test_one_changed_target_hash_byte_is_rejected(self) -> None:
        changed = copy.deepcopy(self.fixture)
        changed["target_hash"] = "8" + changed["target_hash"][1:]
        with self.assertRaises(ApprovalRejected) as raised:
            verify_approval(
                changed,
                SYNTHETIC_FIXTURE_KEY,
                expected_target_hash=self.fixture["target_hash"],
                now=datetime(2026, 9, 26, tzinfo=timezone.utc),
            )
        self.assertEqual(raised.exception.code, "approval_wrong_target_hash")

    def test_reusable_approval_is_rejected_even_when_seal_is_valid(self) -> None:
        reusable = copy.deepcopy(self.fixture)
        reusable["single_use"] = False
        reusable = seal_approval(reusable, SYNTHETIC_FIXTURE_KEY)
        with self.assertRaises(ApprovalRejected) as raised:
            verify_approval(
                reusable,
                SYNTHETIC_FIXTURE_KEY,
                now=datetime(2026, 9, 26, tzinfo=timezone.utc),
            )
        self.assertEqual(raised.exception.code, "approval_not_single_use")

    def test_six_rejection_modes_have_distinct_codes(self) -> None:
        cases: dict[str, tuple[dict, dict]] = {}

        unsigned = copy.deepcopy(self.fixture)
        unsigned["control_plane_seal"] = "0" * 64
        cases["approval_unsigned"] = (unsigned, {})

        expired = copy.deepcopy(self.fixture)
        expired["expires_at"] = "2020-01-01T00:00:00Z"
        cases["approval_expired"] = (seal_approval(expired, SYNTHETIC_FIXTURE_KEY), {})

        wrong_target = copy.deepcopy(self.fixture)
        wrong_target["target_hash"] = "8" + wrong_target["target_hash"][1:]
        cases["approval_wrong_target_hash"] = (
            wrong_target,
            {"expected_target_hash": self.fixture["target_hash"]},
        )

        consumed = copy.deepcopy(self.fixture)
        consumed["consumed_at"] = "2026-09-25T00:00:00Z"
        cases["approval_consumed"] = (consumed, {})

        revoked = copy.deepcopy(self.fixture)
        revoked["revoked_at"] = "2026-09-25T00:00:00Z"
        cases["approval_revoked"] = (revoked, {})

        weaker = copy.deepcopy(self.fixture)
        weaker["authorized_action"] = "invariant_change"
        cases["approval_weaker_method"] = (
            seal_approval(weaker, SYNTHETIC_FIXTURE_KEY),
            {},
        )

        seen = set()
        for expected_code, (record, kwargs) in cases.items():
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(ApprovalRejected) as raised:
                    verify_approval(
                        record,
                        SYNTHETIC_FIXTURE_KEY,
                        now=datetime(2026, 9, 26, tzinfo=timezone.utc),
                        **kwargs,
                    )
                self.assertEqual(raised.exception.code, expected_code)
                seen.add(raised.exception.code)
        self.assertEqual(len(seen), 6)

    def test_concurrent_double_consume_allows_exactly_one_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = EventStore(
                pathlib.Path(temporary) / "runtime.sqlite3", repository_root=self.root
            )
            manager = ApprovalManager(store, key=SYNTHETIC_FIXTURE_KEY)
            record = copy.deepcopy(self.fixture)
            record["approval_id"] = "CONCURRENT-APPROVAL"
            record = seal_approval(record, SYNTHETIC_FIXTURE_KEY)
            manager.register(record)

            def consume() -> str:
                try:
                    manager.consume(
                        record["approval_id"],
                        expected_action=record["authorized_action"],
                        expected_target_hash=record["target_hash"],
                        expected_scope=record["scope"],
                        now=datetime(2026, 9, 26, tzinfo=timezone.utc),
                    )
                    return "accepted"
                except ApprovalRejected as exc:
                    return exc.code

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(lambda _index: consume(), range(2)))
            self.assertEqual(outcomes.count("accepted"), 1)
            self.assertEqual(outcomes.count("approval_consumed"), 1)


class DoctorTests(unittest.TestCase):
    fingerprint = "f" * 16

    def _key_and_policy(self, directory: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
        key = directory / "seal.key"
        key.write_bytes(b"k" * 32)
        key.chmod(0o600)
        import hashlib

        fingerprint = hashlib.sha256(key.read_bytes()).hexdigest()[:16]
        policy = directory / "principals.json"
        policy.write_text(
            json.dumps(
                {
                    "principals": [
                        {
                            "principal_id": "owner",
                            "seal_key_fingerprint": fingerprint,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return key, policy

    def test_valid_external_mode_0600_recorded_key_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as repo:
            key, policy = self._key_and_policy(pathlib.Path(temporary))
            result = doctor(
                repository_root=pathlib.Path(repo),
                key_path=key,
                principals_path=policy,
                first_approval_commit_time=key.stat().st_mtime + 1,
            )
            self.assertEqual(result["status"], "ok")

    def test_key_inside_worktree_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as repo:
            root = pathlib.Path(repo)
            key, policy = self._key_and_policy(root)
            with self.assertRaises(PermissionViolation):
                doctor(
                    repository_root=root,
                    key_path=key,
                    principals_path=policy,
                    first_approval_commit_time=key.stat().st_mtime + 1,
                )

    def test_mode_wider_than_0600_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as repo:
            key, policy = self._key_and_policy(pathlib.Path(temporary))
            key.chmod(0o644)
            with self.assertRaises(ConfigError) as raised:
                doctor(
                    repository_root=pathlib.Path(repo),
                    key_path=key,
                    principals_path=policy,
                    first_approval_commit_time=key.stat().st_mtime + 1,
                )
            self.assertEqual(raised.exception.context["reason_code"], "seal_key_mode")

    def test_unrecorded_fingerprint_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as repo:
            key, policy = self._key_and_policy(pathlib.Path(temporary))
            policy.write_text(json.dumps({"principals": []}), encoding="utf-8")
            with self.assertRaises(ConfigError) as raised:
                doctor(
                    repository_root=pathlib.Path(repo),
                    key_path=key,
                    principals_path=policy,
                    first_approval_commit_time=key.stat().st_mtime + 1,
                )
            self.assertEqual(raised.exception.context["reason_code"], "fingerprint_unrecorded")

    def test_key_newer_than_first_approval_commit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as repo:
            key, policy = self._key_and_policy(pathlib.Path(temporary))
            with self.assertRaises(ConfigError) as raised:
                doctor(
                    repository_root=pathlib.Path(repo),
                    key_path=key,
                    principals_path=policy,
                    first_approval_commit_time=key.stat().st_mtime - 1,
                )
            self.assertEqual(raised.exception.context["reason_code"], "seal_key_too_new")


if __name__ == "__main__":
    unittest.main()
