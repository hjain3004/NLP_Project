"""The one signature every worker is driven through.

    run(role, context_pack, task_contract, tool_policy, budget, output_schema)
        -> RunResult

Automated providers and a human operator implement the *same* call. At P0 only
:class:`ManualProposalAdapter` is concrete: it serialises a dispatch pack to
disk and reads a returned artifact file back. Fixing this protocol now is what
lets Level-3 automation drop in behind it without the control plane changing.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

__all__ = ["RunResult", "Adapter", "ManualProposalAdapter"]


@dataclass(frozen=True)
class RunResult:
    """Everything the control plane needs to judge one worker invocation.

    It never carries authoritative state — ``agentctl`` decides what a result
    means. ``status`` is the coarse outcome; ``termination_reason`` mirrors the
    implementation-manifest enum
    (``completed|budget_exhausted|blocked|escalated|canceled|error``).
    """

    provider: str
    model: str
    model_version: str
    task_id: str
    attempt_id: str
    context_hash: str
    contract_hash: str
    status: str
    termination_reason: str
    output_artifacts: tuple[Mapping[str, Any], ...] = ()
    tool_trace_refs: tuple[str, ...] = ()
    usage: Mapping[str, Any] = field(default_factory=dict)
    result_commit: str | None = None
    verdict: str | None = None
    unresolved_errors: tuple[str, ...] = ()


class Adapter(ABC):
    """Base class for every worker adapter."""

    provider: str = "unset"

    @abstractmethod
    def run(
        self,
        role: str,
        context_pack: Mapping[str, Any],
        task_contract: Mapping[str, Any],
        tool_policy: Mapping[str, Any],
        budget: Mapping[str, Any],
        output_schema: Mapping[str, Any],
    ) -> RunResult:
        """Drive one bounded worker invocation and return its typed result."""
        raise NotImplementedError


class ManualProposalAdapter(Adapter):
    """Writes a dispatch pack for a human worker; reads the reply back.

    ``proposal_only`` enforcement: this adapter has no shell or repository
    access. It stages inputs under ``dispatch_root/<task_id>/`` and, once a
    human has dropped ``returned-artifact.json`` beside them, ``collect`` reads
    exactly that file.
    """

    provider = "manual"

    _PACK_FILES = {
        "role": "role.txt",
        "context_pack": "context-pack.json",
        "task_contract": "task-contract.json",
        "tool_policy": "tool-policy.json",
        "budget": "budget.json",
        "output_schema": "output-schema.json",
    }
    _RETURN_FILE = "returned-artifact.json"

    def __init__(self, dispatch_root: str | Path) -> None:
        self.dispatch_root = Path(dispatch_root)

    def _pack_dir(self, task_id: str) -> Path:
        return self.dispatch_root / task_id

    def run(
        self,
        role: str,
        context_pack: Mapping[str, Any],
        task_contract: Mapping[str, Any],
        tool_policy: Mapping[str, Any],
        budget: Mapping[str, Any],
        output_schema: Mapping[str, Any],
    ) -> RunResult:
        pack_dir = self._pack_dir(str(task_contract.get("task_id", "unknown")))
        pack_dir.mkdir(parents=True, exist_ok=True)
        (pack_dir / self._PACK_FILES["role"]).write_text(role + "\n", encoding="utf-8")
        for key, blob in (
            ("context_pack", context_pack),
            ("task_contract", task_contract),
            ("tool_policy", tool_policy),
            ("budget", budget),
            ("output_schema", output_schema),
        ):
            (pack_dir / self._PACK_FILES[key]).write_text(
                json.dumps(blob, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        return RunResult(
            provider=self.provider,
            model="human",
            model_version="n/a",
            task_id=str(task_contract.get("task_id", "unknown")),
            attempt_id=str(context_pack.get("attempt_id", "unknown")),
            context_hash=str(context_pack.get("context_hash", "")),
            contract_hash=str(task_contract.get("contract_hash", "")),
            status="dispatched",
            termination_reason="blocked",
            unresolved_errors=("awaiting manual artifact return",),
        )

    def collect(self, task_id: str) -> dict[str, Any]:
        """Read the artifact a human placed in the dispatch directory."""
        path = self._pack_dir(task_id) / self._RETURN_FILE
        return json.loads(path.read_text(encoding="utf-8"))
