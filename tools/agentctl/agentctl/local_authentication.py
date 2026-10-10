"""Fail-closed macOS LocalAuthentication support for consequential approvals.

The adapter receives only the typed action, exact SHA-256 target hash, and a
human-readable reason. It never receives or returns a seal key, and its narrow
interface makes tests independent of biometric prompts.
"""

from __future__ import annotations

import enum
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from .approval import ApprovalRejected

__all__ = [
    "LocalAuthenticationAdapter",
    "LocalAuthenticationResult",
    "MacOSLocalAuthentication",
    "authenticate_exact_hash",
]


class LocalAuthenticationResult(str, enum.Enum):
    AUTHENTICATED = "authenticated"
    CANCELLED = "cancelled"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class LocalAuthenticationAdapter(Protocol):
    """Minimal injected boundary around platform authentication."""

    def authenticate(
        self,
        *,
        action: str,
        target_hash: str,
        reason: str,
        approval_id: str | None = None,
        nonce: str | None = None,
    ) -> LocalAuthenticationResult: ...


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def authenticate_exact_hash(
    adapter: LocalAuthenticationAdapter,
    *,
    action: str,
    target_hash: str,
    reason: str,
    approval_id: str | None = None,
    nonce: str | None = None,
) -> None:
    """Require local authentication for one exact target hash.

    Any unavailable, cancelled, failed, malformed, or adapter-error state is a
    rejection. No fallback authentication is attempted.
    """
    if not isinstance(target_hash, str) or _SHA256.fullmatch(target_hash) is None:
        raise ApprovalRejected(
            "approval_wrong_target_hash", "target hash must be lowercase SHA-256"
        )
    if (
        not isinstance(action, str)
        or not action.strip()
        or not isinstance(reason, str)
        or not reason.strip()
    ):
        raise ApprovalRejected(
            "approval_authentication_failed", "local authentication request is malformed"
        )
    if (approval_id is None) != (nonce is None):
        raise ApprovalRejected(
            "approval_authentication_failed", "approval binding is malformed"
        )
    if approval_id is not None and (
        not approval_id.strip() or not isinstance(nonce, str)
    ):
        raise ApprovalRejected(
            "approval_authentication_failed", "approval binding is malformed"
        )
    if approval_id is not None and (
        len(nonce or "") != 32
        or any(character not in "0123456789abcdef" for character in nonce or "")
    ):
        raise ApprovalRejected(
            "approval_authentication_failed", "approval binding is malformed"
        )
    request = {"action": action, "target_hash": target_hash, "reason": reason}
    if approval_id is not None:
        request.update({"approval_id": approval_id, "nonce": nonce})
    try:
        outcome = adapter.authenticate(**request)
    except Exception as exc:  # platform adapters must fail closed
        raise ApprovalRejected(
            "approval_authentication_unavailable",
            "local authentication is unavailable",
        ) from exc
    if outcome == LocalAuthenticationResult.AUTHENTICATED:
        return
    if outcome == LocalAuthenticationResult.CANCELLED:
        raise ApprovalRejected(
            "approval_authentication_cancelled", "local authentication was cancelled"
        )
    if outcome == LocalAuthenticationResult.UNAVAILABLE:
        raise ApprovalRejected(
            "approval_authentication_unavailable", "local authentication is unavailable"
        )
    raise ApprovalRejected(
        "approval_authentication_failed", "local authentication failed"
    )


class MacOSLocalAuthentication:
    """Run a bundled LocalAuthentication.framework helper on macOS.

    ``runner`` and ``platform`` are injectable so tests never invoke a live
    biometric or passcode prompt. The helper receives no secret material.
    """

    def __init__(
        self,
        *,
        helper: str | Path | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        platform: str | None = None,
    ) -> None:
        self.helper = Path(helper) if helper is not None else Path(__file__).with_name(
            "local_authentication.swift"
        )
        self.runner = runner or subprocess.run
        self.platform = platform or sys.platform

    def authenticate(
        self,
        *,
        action: str,
        target_hash: str,
        reason: str,
        approval_id: str | None = None,
        nonce: str | None = None,
    ) -> LocalAuthenticationResult:
        if self.platform != "darwin":
            return LocalAuthenticationResult.UNAVAILABLE
        if not self.helper.is_file():
            return LocalAuthenticationResult.UNAVAILABLE
        if (approval_id is None) != (nonce is None):
            return LocalAuthenticationResult.FAILED
        if approval_id is not None and (
            not approval_id.strip()
            or len(nonce or "") != 32
            or any(character not in "0123456789abcdef" for character in nonce or "")
        ):
            return LocalAuthenticationResult.FAILED
        command = ["/usr/bin/swift", str(self.helper), action, target_hash, reason]
        if approval_id is not None:
            command.extend([approval_id, nonce or ""])
        try:
            completed = self.runner(
                command,
                capture_output=True,
                check=False,
                text=True,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError):
            return LocalAuthenticationResult.UNAVAILABLE
        if completed.returncode == 0 and completed.stdout.strip() == "authenticated":
            return LocalAuthenticationResult.AUTHENTICATED
        if completed.returncode == 2:
            return LocalAuthenticationResult.CANCELLED
        return LocalAuthenticationResult.FAILED
