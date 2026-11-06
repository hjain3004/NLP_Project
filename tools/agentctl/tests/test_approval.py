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
from types import SimpleNamespace
from unittest import mock

from agentctl.approval import (
    SYNTHETIC_FIXTURE_KEY,
    ApprovalManager,
    ApprovalRejected,
    doctor,
    seal_approval,
    verify_approval,
)
from agentctl.local_authentication import (
    LocalAuthenticationResult,
    MacOSLocalAuthentication,
    authenticate_exact_hash,
)
from agentctl.commands.approve import _issue_record
from agentctl.commands import approve as approve_command
from agentctl.config import Config, PermissionViolation
from agentctl.errors import ConfigError
from agentctl.hashing import sha256_of_json
from agentctl.store import EventStore


def _repo_root() -> pathlib.Path:
    return pathlib.Path(
        subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
    )


class ApprovalTests(unittest.TestCase):
    root = _repo_root()

    def setUp(self) -> None:
        self.committed_fixture = json.loads(
            (self.root / "agent-system/examples/valid/08-human-approval.json").read_text(
                encoding="utf-8"
            )
        )
        self.fixture = copy.deepcopy(self.committed_fixture)
        self.fixture["authorized_action"] = "phase_authorization"
        self.fixture["target_type"] = "phase-gate"
        self.fixture["target_id"] = "EX-GATE-001"
        self.fixture["target_hash"] = "834625540822aac70d819972077cb28ca2614d3ab12951cf7b6298185198876a"
        self.fixture["scope"] = "Synthetic acceptance fixture only"
        self.fixture["reason"] = "Exercise the positive approval-validation path with a public synthetic fixture key"
        self.fixture["authentication_method"] = "interactive_tty_with_key_unlock"
        self.fixture = seal_approval(self.fixture, SYNTHETIC_FIXTURE_KEY)

    def test_committed_valid_fixture_is_cryptographically_accepted(self) -> None:
        verified = verify_approval(
            self.committed_fixture,
            SYNTHETIC_FIXTURE_KEY,
            expected_action=self.committed_fixture["authorized_action"],
            expected_target_hash=self.committed_fixture["target_hash"],
            expected_scope=self.committed_fixture["scope"],
            now=datetime(2026, 9, 26, tzinfo=timezone.utc),
        )
        self.assertEqual(verified["approval_id"], "EX-APPROVAL-001")

    def test_local_authentication_adapter_receives_exact_target_hash(self) -> None:
        calls: list[dict[str, str]] = []

        class Adapter:
            def authenticate(self, **kwargs: str) -> LocalAuthenticationResult:
                calls.append(kwargs)
                return LocalAuthenticationResult.AUTHENTICATED

        target_hash = "a" * 64
        authenticate_exact_hash(
            Adapter(),
            action="invariant_change",
            target_hash=target_hash,
            reason="Approve the exact invariant change",
        )
        self.assertEqual(calls, [{
            "action": "invariant_change",
            "target_hash": target_hash,
            "reason": "Approve the exact invariant change",
        }])

    def test_cancelled_local_authentication_fails_closed(self) -> None:
        class Adapter:
            def authenticate(self, **kwargs: str) -> LocalAuthenticationResult:
                return LocalAuthenticationResult.CANCELLED

        with self.assertRaises(ApprovalRejected) as raised:
            authenticate_exact_hash(
                Adapter(),
                action="invariant_change",
                target_hash="b" * 64,
                reason="Approve the exact invariant change",
            )
        self.assertEqual(raised.exception.code, "approval_authentication_cancelled")

    def test_unavailable_local_authentication_fails_closed_without_secrets(self) -> None:
        class Adapter:
            def authenticate(self, **kwargs: str) -> LocalAuthenticationResult:
                return LocalAuthenticationResult.UNAVAILABLE

        with self.assertRaises(ApprovalRejected) as raised:
            authenticate_exact_hash(
                Adapter(),
                action="invariant_change",
                target_hash="c" * 64,
                reason="Approve the exact invariant change",
            )
        self.assertEqual(raised.exception.code, "approval_authentication_unavailable")

    def test_local_authentication_rejects_non_sha256_target_before_adapter(self) -> None:
        calls = 0

        class Adapter:
            def authenticate(self, **kwargs: str) -> LocalAuthenticationResult:
                nonlocal calls
                calls += 1
                return LocalAuthenticationResult.AUTHENTICATED

        with self.assertRaises(ApprovalRejected) as raised:
            authenticate_exact_hash(
                Adapter(),
                action="invariant_change",
                target_hash="not-a-hash",
                reason="Approve the exact invariant change",
            )
        self.assertEqual(raised.exception.code, "approval_wrong_target_hash")
        self.assertEqual(calls, 0)

    def test_macos_adapter_maps_helper_success_without_exposing_output(self) -> None:
        calls: list[tuple[list[str], dict]] = []

        def runner(command: list[str], **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, "authenticated\n", "secret-like stderr")

        adapter = MacOSLocalAuthentication(
            helper=self.root / "agent-system/work/P0/gates/G0-criteria.json",
            runner=runner,
            platform="darwin",
        )
        result = adapter.authenticate(
            action="invariant_change", target_hash="d" * 64, reason="Exact hash approval"
        )
        self.assertEqual(result, LocalAuthenticationResult.AUTHENTICATED)
        self.assertEqual(calls[0][0][0], "/usr/bin/swift")
        self.assertEqual(calls[0][0][2:], ["invariant_change", "d" * 64, "Exact hash approval"])

    def test_strong_issuance_authenticates_binding_before_seal(self) -> None:
        calls: list[dict[str, str]] = []

        class Adapter:
            def authenticate(self, **kwargs: str) -> LocalAuthenticationResult:
                calls.append(kwargs.copy())
                # A helper cannot rewrite the immutable provisional record: the
                # issuer passes a copy of each bound scalar.
                kwargs["target_hash"] = "0" * 64
                return LocalAuthenticationResult.AUTHENTICATED

        policy = {"tiers": {"schema_commitment": ["keychain_backed", "local_authentication"]}}
        record = _issue_record(
            key=SYNTHETIC_FIXTURE_KEY,
            principal="owner",
            machine_identity="machine",
            action="schema_commitment",
            target_type="schema",
            target_id="SCHEMA-1",
            target_hash="a" * 64,
            scope="exact schema",
            reason="Approve exact schema",
            policy=policy,
            authentication_adapter=Adapter(),
        )
        self.assertEqual(record["authentication_method"], "local_authentication")
        self.assertEqual(calls[0]["approval_id"], record["approval_id"])
        self.assertEqual(calls[0]["nonce"], record["nonce"])
        self.assertEqual(calls[0]["action"], "schema_commitment")
        self.assertEqual(calls[0]["target_type"], "schema")
        self.assertEqual(calls[0]["target_id"], "SCHEMA-1")
        self.assertEqual(calls[0]["target_hash"], "a" * 64)
        self.assertEqual(calls[0]["scope"], "exact schema")
        self.assertTrue(calls[0]["reason"].startswith("Authorize this exact approval; reason SHA-256:"))
        verify_approval(
            record,
            SYNTHETIC_FIXTURE_KEY,
            expected_action="schema_commitment",
            expected_target_hash="a" * 64,
            expected_scope="exact schema",
            policy=policy,
        )

    def test_strong_issuance_cancellation_produces_no_sealed_record(self) -> None:
        class Adapter:
            def authenticate(self, **kwargs: str) -> LocalAuthenticationResult:
                return LocalAuthenticationResult.CANCELLED

        with self.assertRaises(ApprovalRejected) as raised:
            _issue_record(
                key=SYNTHETIC_FIXTURE_KEY,
                principal="owner",
                machine_identity="machine",
                action="schema_commitment",
                target_type="schema",
                target_id="SCHEMA-1",
                target_hash="a" * 64,
                scope="exact schema",
                reason="Approve exact schema",
                policy={"tiers": {"schema_commitment": ["local_authentication"]}},
                authentication_adapter=Adapter(),
            )
        self.assertEqual(raised.exception.code, "approval_authentication_cancelled")

    def test_local_authentication_reads_seal_key_only_after_success(self) -> None:
        state = {"authenticated": False}

        class Adapter:
            def authenticate(self, **kwargs: str) -> LocalAuthenticationResult:
                state["authenticated"] = True
                return LocalAuthenticationResult.AUTHENTICATED

        def load_key() -> bytes:
            self.assertTrue(state["authenticated"])
            return SYNTHETIC_FIXTURE_KEY

        record = _issue_record(
            key=None,
            seal_key_loader=load_key,
            principal="owner",
            machine_identity="machine",
            action="schema_commitment",
            target_type="schema",
            target_id="SCHEMA-1",
            target_hash="a" * 64,
            scope="exact schema",
            reason="Approve exact schema",
            policy={"tiers": {"schema_commitment": ["local_authentication"]}},
            authentication_adapter=Adapter(),
        )
        self.assertEqual(record["authentication_method"], "local_authentication")

    def test_principal_method_permission_is_required_when_policy_is_supplied(self) -> None:
        with self.assertRaises(ApprovalRejected) as raised:
            _issue_record(
                key=SYNTHETIC_FIXTURE_KEY,
                principal="owner",
                machine_identity="machine",
                action="schema_commitment",
                target_type="schema",
                target_id="SCHEMA-1",
                target_hash="a" * 64,
                scope="exact schema",
                reason="Approve exact schema",
                policy={"tiers": {"schema_commitment": ["local_authentication"]}},
                principal_policy={
                    "principals": [
                        {
                            "principal_id": "owner",
                            "authentication_methods": ["interactive_tty_with_key_unlock"],
                        }
                    ]
                },
            )
        self.assertEqual(raised.exception.code, "approval_weaker_method")

    def test_unregistered_principal_is_rejected(self) -> None:
        with self.assertRaises(ApprovalRejected) as raised:
            _issue_record(
                key=SYNTHETIC_FIXTURE_KEY,
                principal="unregistered-principal",
                machine_identity="machine",
                action="schema_commitment",
                target_type="schema",
                target_id="SCHEMA-1",
                target_hash="a" * 64,
                scope="exact schema",
                reason="Approve exact schema",
                policy={"tiers": {"schema_commitment": ["local_authentication"]}},
                principal_policy={
                    "principals": [
                        {
                            "principal_id": "himanshu_jain",
                            "authentication_methods": ["local_authentication"],
                        }
                    ]
                },
            )
        self.assertEqual(raised.exception.code, "approval_principal_unknown")

    def test_malformed_helper_output_is_not_authentication(self) -> None:
        def runner(command: list[str], **kwargs):
            return subprocess.CompletedProcess(command, 0, "authenticated\nextra\n", "")

        adapter = MacOSLocalAuthentication(
            helper=self.root / "agent-system/work/P0/gates/G0-criteria.json",
            runner=runner,
            platform="darwin",
        )
        with self.assertRaises(ApprovalRejected) as raised:
            authenticate_exact_hash(
                adapter,
                action="schema_commitment",
                target_hash="a" * 64,
                reason="Approve exact schema",
                approval_id="approval-1",
                nonce="0123456789abcdef0123456789abcdef",
            )
        self.assertEqual(raised.exception.code, "approval_authentication_failed")

    def test_unsupported_strong_method_is_hard_refused(self) -> None:
        with self.assertRaises(ApprovalRejected) as raised:
            _issue_record(
                key=SYNTHETIC_FIXTURE_KEY,
                principal="owner",
                machine_identity="machine",
                action="schema_commitment",
                target_type="schema",
                target_id="SCHEMA-1",
                target_hash="a" * 64,
                scope="exact schema",
                reason="Approve exact schema",
                policy={"tiers": {"schema_commitment": ["keychain_backed"]}},
            )
        self.assertEqual(raised.exception.code, "approval_weaker_method")

    def test_verifier_rejects_every_bound_field_mutation(self) -> None:
        policy = {"tiers": {"schema_commitment": ["local_authentication"]}}
        record = _issue_record(
            key=SYNTHETIC_FIXTURE_KEY,
            principal="owner",
            machine_identity="machine",
            action="schema_commitment",
            target_type="schema",
            target_id="SCHEMA-1",
            target_hash="a" * 64,
            scope="exact schema",
            reason="Approve exact schema",
            policy=policy,
            authentication_adapter=mock.Mock(
                authenticate=lambda **kwargs: LocalAuthenticationResult.AUTHENTICATED
            ),
        )
        expectations = {
            "authorized_action": "schema_commitment",
            "target_type": "schema",
            "target_id": "SCHEMA-1",
            "target_hash": "a" * 64,
            "scope": "exact schema",
        }
        for field, replacement in {
            "authorized_action": "invariant_change",
            "target_type": "other-schema",
            "target_id": "SCHEMA-2",
            "target_hash": "b" * 64,
            "scope": "other scope",
        }.items():
            with self.subTest(field=field):
                changed = copy.deepcopy(record)
                changed[field] = replacement
                changed = seal_approval(changed, SYNTHETIC_FIXTURE_KEY)
                with self.assertRaises(ApprovalRejected):
                    verify_approval(
                        changed,
                        SYNTHETIC_FIXTURE_KEY,
                        expected_action=expectations["authorized_action"],
                        expected_target_type=expectations["target_type"],
                        expected_target_id=expectations["target_id"],
                        expected_target_hash=expectations["target_hash"],
                        expected_scope=expectations["scope"],
                        policy=policy,
                    )

    def test_production_cli_uses_policy_and_registers_strong_record(self) -> None:
        key = b"k" * 32
        calls: list[dict[str, str]] = []
        registered: list[dict] = []

        class Adapter:
            def authenticate(self, **kwargs: str) -> LocalAuthenticationResult:
                calls.append(kwargs)
                return LocalAuthenticationResult.AUTHENTICATED

        class Manager:
            def __init__(self, store, *, key):
                self.key = key

            def register(self, record, *, path):
                registered.append({"record": record, "path": path})
                return {"state": "registered"}

        class Digest:
            def hexdigest(self) -> str:
                return "ffcc68362fffeb7b"

        import hashlib as hashlib_module

        real_sha256 = hashlib_module.sha256

        def fake_sha256(data=b""):
            if data == key:
                return Digest()
            return real_sha256(data)

        with tempfile.TemporaryDirectory() as directory:
            temporary_root = pathlib.Path(directory).resolve()
            (temporary_root / "agent-system/policies").mkdir(parents=True)
            for policy_name in ("approval-policy.json", "approval-principals.json"):
                source = self.root / "agent-system/policies" / policy_name
                destination = temporary_root / "agent-system/policies" / policy_name
                destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            output = temporary_root / "strong-approval.json"
            ordinary_output = temporary_root / "ordinary-approval.json"
            (temporary_root / "outside.key").write_bytes(key)
            args = [
                "schema_commitment",
                "--target-type",
                "schema",
                "--target-id",
                "SCHEMA-1",
                "--target-hash",
                "a" * 64,
                "--scope",
                "exact schema",
                "--reason",
                "Approve exact schema",
                "--auth-method",
                "local_authentication",
                "--out",
                str(output),
            ]
            with (
                mock.patch.object(approve_command.sys, "stdin", SimpleNamespace(isatty=lambda: True)),
                mock.patch.object(
                    approve_command,
                    "load_config",
                    return_value=Config(
                        repo_root=temporary_root,
                        runtime_dir=temporary_root / "runtime",
                        seal_key_path=temporary_root / "outside.key",
                    ),
                ),
                mock.patch.object(approve_command, "doctor", return_value={"fingerprint": "ffcc68362fffeb7b"}),
                mock.patch.object(approve_command.getpass, "getpass", return_value=key.hex()),
                mock.patch.object(approve_command, "MacOSLocalAuthentication", return_value=Adapter()),
                mock.patch.object(approve_command, "ApprovalManager", Manager),
                mock.patch.object(approve_command, "EventStore", return_value=object()),
                mock.patch.object(approve_command.hashlib, "sha256", side_effect=fake_sha256),
            ):
                self.assertEqual(approve_command.run(args), 0)
                ordinary_args = [
                    "phase_authorization",
                    "--target-type",
                    "phase-gate",
                    "--target-id",
                    "GATE-1",
                    "--target-hash",
                    "b" * 64,
                    "--scope",
                    "exact gate",
                    "--reason",
                    "Approve exact gate",
                    "--out",
                    str(ordinary_output),
                ]
                self.assertEqual(approve_command.run(ordinary_args), 0)
            self.assertEqual(registered[1]["record"]["authentication_method"], "interactive_tty_with_key_unlock")
            self.assertTrue(ordinary_output.is_file())
        self.assertEqual(len(registered), 2)
        self.assertEqual(registered[0]["record"]["authentication_method"], "local_authentication")
        self.assertEqual(calls[0]["target_type"], "schema")
        self.assertEqual(calls[0]["target_id"], "SCHEMA-1")
        self.assertEqual(calls[0]["scope"], "exact schema")

    def test_preflight_rejects_malformed_and_stale_targets_without_prompts(self) -> None:
        proposal = json.loads(
            (self.root / "agent-system/work/P0/proposals/governance-change-set.json").read_text()
        )
        with (
            mock.patch.object(approve_command, "doctor") as doctor,
            mock.patch.object(approve_command.getpass, "getpass") as getpass,
        ):
            for target_hash, expected_code in (("not-a-hash", "approval_wrong_target_hash"), ("0" * 64, "approval_target_stale")):
                with self.subTest(target_hash=target_hash):
                    args = [
                        "schema_commitment",
                        "--target-type",
                        "governance-change-set",
                        "--target-id",
                        proposal["proposal_id"],
                        "--target-hash",
                        target_hash,
                        "--reason",
                        "Approve exact governance change set",
                        "--scope",
                        "exact proposal bytes",
                        "--preflight",
                        "--auth-method",
                        "local_authentication",
                    ]
                    with self.assertRaises(ApprovalRejected) as raised:
                        approve_command.run(args)
                    self.assertEqual(raised.exception.code, expected_code)
            doctor.assert_not_called()
            getpass.assert_not_called()

    def test_production_cli_auth_failure_emits_no_record(self) -> None:
        key = b"k" * 32
        registered: list[dict] = []

        class CancelAdapter:
            def authenticate(self, **kwargs: str) -> LocalAuthenticationResult:
                return LocalAuthenticationResult.CANCELLED

        class Manager:
            def __init__(self, store, *, key):
                pass

            def register(self, record, *, path):
                registered.append(record)

        class Digest:
            def hexdigest(self) -> str:
                return "ffcc68362fffeb7b"

        import hashlib as hashlib_module

        real_sha256 = hashlib_module.sha256

        def fake_sha256(data=b""):
            return Digest() if data == key else real_sha256(data)

        with tempfile.TemporaryDirectory() as directory:
            temporary_root = pathlib.Path(directory).resolve()
            policy_root = temporary_root / "agent-system/policies"
            policy_root.mkdir(parents=True)
            for policy_name in ("approval-policy.json", "approval-principals.json"):
                source = self.root / "agent-system/policies" / policy_name
                (policy_root / policy_name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            output = temporary_root / "cancelled.json"
            args = [
                "schema_commitment", "--target-type", "schema", "--target-id", "SCHEMA-1",
                "--target-hash", "a" * 64, "--scope", "exact schema", "--reason", "Approve exact schema",
                "--auth-method", "local_authentication", "--out", str(output),
            ]
            with (
                mock.patch.object(approve_command.sys, "stdin", SimpleNamespace(isatty=lambda: True)),
                mock.patch.object(approve_command, "load_config", return_value=Config(temporary_root, temporary_root / "runtime", temporary_root / "outside.key")),
                mock.patch.object(approve_command, "doctor", return_value={"fingerprint": "ffcc68362fffeb7b"}),
                mock.patch.object(approve_command.getpass, "getpass", return_value=key.hex()),
                mock.patch.object(approve_command, "MacOSLocalAuthentication", return_value=CancelAdapter()),
                mock.patch.object(approve_command, "ApprovalManager", Manager),
                mock.patch.object(approve_command, "EventStore", return_value=object()),
                mock.patch.object(approve_command.hashlib, "sha256", side_effect=fake_sha256),
            ):
                with self.assertRaises(ApprovalRejected) as raised:
                    approve_command.run(args)
            self.assertEqual(raised.exception.code, "approval_authentication_cancelled")
            self.assertFalse(output.exists())
            self.assertEqual(registered, [])

    def test_auth_method_flag_cannot_select_a_weaker_method(self) -> None:
        with self.assertRaises(ApprovalRejected) as raised:
            _issue_record(
                key=SYNTHETIC_FIXTURE_KEY,
                principal="owner",
                machine_identity="machine",
                action="schema_commitment",
                target_type="schema",
                target_id="SCHEMA-1",
                target_hash="a" * 64,
                scope="exact schema",
                reason="Approve exact schema",
                policy={"tiers": {"schema_commitment": ["keychain_backed", "local_authentication"]}},
                requested_authentication_method="interactive_tty_with_key_unlock",
            )
        self.assertEqual(raised.exception.code, "approval_weaker_method")

    def test_consequential_adr_acceptance_requires_strong_authentication(self) -> None:
        record = copy.deepcopy(self.committed_fixture)
        record["authorized_action"] = "consequential_adr_acceptance"
        record["target_type"] = "adr"
        record["target_id"] = "ADR-0005"
        record["target_hash"] = "672940b94ea9becca0aeb9d1fa92ff2405054f367bf2bccb2052f9e8d11a09bd"
        record["authentication_method"] = "interactive_tty_with_key_unlock"
        record = seal_approval(record, SYNTHETIC_FIXTURE_KEY)
        with self.assertRaises(ApprovalRejected) as raised:
            verify_approval(
                record,
                SYNTHETIC_FIXTURE_KEY,
                expected_action="consequential_adr_acceptance",
                policy={"tiers": {"consequential_adr_acceptance": ["keychain_backed", "local_authentication"]}},
            )
        self.assertEqual(raised.exception.code, "approval_weaker_method")

    def test_ordinary_tty_issuance_remains_unchanged(self) -> None:
        record = _issue_record(
            key=SYNTHETIC_FIXTURE_KEY,
            principal="owner",
            machine_identity="machine",
            action="phase_authorization",
            target_type="phase_gate",
            target_id="GATE-1",
            target_hash="a" * 64,
            scope="exact gate",
            reason="Approve exact gate",
            policy={"tiers": {"phase_authorization": "interactive_tty_with_key_unlock"}},
        )
        self.assertEqual(record["authentication_method"], "interactive_tty_with_key_unlock")
        verify_approval(
            record,
            SYNTHETIC_FIXTURE_KEY,
            expected_action="phase_authorization",
            expected_target_hash="a" * 64,
            expected_scope="exact gate",
            policy={"tiers": {"phase_authorization": "interactive_tty_with_key_unlock"}},
        )

    def test_one_changed_target_hash_byte_is_rejected(self) -> None:
        changed = copy.deepcopy(self.fixture)
        replacement = "0" if changed["target_hash"][0] != "0" else "1"
        changed["target_hash"] = replacement + changed["target_hash"][1:]
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
        replacement = "0" if wrong_target["target_hash"][0] != "0" else "1"
        wrong_target["target_hash"] = replacement + wrong_target["target_hash"][1:]
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
