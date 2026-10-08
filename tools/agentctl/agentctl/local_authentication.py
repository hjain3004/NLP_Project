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
        self, *, action: str, target_hash: str, reason: str
    ) -> LocalAuthenticationResult: ...


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def authenticate_exact_hash(
    adapter: LocalAuthenticationAdapter,
    *,
    action: str,
    target_hash: str,
    reason: str,
) -> None:
    """Require local authentication for one exact target hash.

    Any unavailable, cancelled, failed, malformed, or adapter-error state is a
    rejection. No fallback authentication is attempted.
    """
    if not isinstance(target_hash, str) or _SHA256.fullmatch(target_hash) is None:
        raise ApprovalRejected(
            "approval_wrong_target_hash", "target hash must be lowercase SHA-256"
        )
    if not isinstance(action, str) or not action.strip() or not isinstance(reason, str) or not reason.strip():
        raise ApprovalRejected(
            "approval_authentication_failed", "local authentication request is malformed"
        )
    try:
        outcome = adapter.authenticate(
            action=action, target_hash=target_hash, reason=reason
        )
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
        self, *, action: str, target_hash: str, reason: str
    ) -> LocalAuthenticationResult:
        if self.platform != "darwin":
            return LocalAuthenticationResult.UNAVAILABLE
        if not self.helper.is_file():
            return LocalAuthenticationResult.UNAVAILABLE
        try:
            completed = self.runner(
                ["/usr/bin/swift", str(self.helper), action, target_hash, reason],
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
