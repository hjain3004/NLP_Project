"""Provider-neutral worker adapters.

The control plane speaks to every worker through one signature (``adapters.base``).
Two concrete adapters exist: ``ManualProposalAdapter`` (a human drops a returned
artifact file beside a dispatch pack) and ``ClaudeCliAdapter`` (one tool-less
``claude -p`` process per call). ``ADAPTER_BINDINGS`` is the *only* place a provider
name is tied to an adapter; a provider absent from it is routed as
``unsupported_provider`` and is never substituted by another one.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import claude_cli
from .base import Adapter, ManualProposalAdapter, RunResult
from .claude_cli import ClaudeCliAdapter

__all__ = [
    "ADAPTER_BINDINGS",
    "Adapter",
    "AdapterBinding",
    "ClaudeCliAdapter",
    "ManualProposalAdapter",
    "RunResult",
    "build_adapter",
]


@dataclass(frozen=True)
class AdapterBinding:
    provider: str
    adapter_name: str
    executable: str


ADAPTER_BINDINGS: dict[str, AdapterBinding] = {
    claude_cli.PROVIDER: AdapterBinding(
        provider=claude_cli.PROVIDER,
        adapter_name=claude_cli.ADAPTER_NAME,
        executable=claude_cli.EXECUTABLE_NAME,
    ),
}


def build_adapter(provider: str, model_id: str, *, executable: str) -> Adapter:
    """Construct the adapter bound to ``provider``; unknown providers have none."""
    if provider == claude_cli.PROVIDER:
        return ClaudeCliAdapter(model_id, executable=executable)
    raise KeyError(f"no adapter is bound to provider {provider!r}")
