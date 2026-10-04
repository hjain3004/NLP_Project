"""Versioned interface-source bindings used by contracts and context packs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import PlanningError
from .hashing import sha256_of_file
from .store import EventStore

__all__ = ["InterfaceBinding", "current_interface_binding", "policy_interface_binding"]


@dataclass(frozen=True)
class InterfaceBinding:
    interface_id: str
    version: str
    path: str
    content_hash: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "interface_id": self.interface_id,
            "version": self.version,
            "path": self.path,
            "content_hash": self.content_hash,
        }


def _binding_from_source(
    root: Path, interface_id: str, version: str, relative_path: str
) -> InterfaceBinding:
    source = (root / relative_path).resolve()
    try:
        normalized = source.relative_to(root).as_posix()
    except ValueError as exc:
        raise PlanningError(
            "interface source escapes the repository", interface_id=interface_id
        ) from exc
    if normalized != relative_path or not source.is_file():
        raise PlanningError(
            "interface source is missing or unnormalised",
            interface_id=interface_id,
            path=relative_path,
        )
    return InterfaceBinding(interface_id, str(version), relative_path, sha256_of_file(source))


def policy_interface_binding(root: Path, interface_id: str) -> InterfaceBinding:
    policy = json.loads(
        (root / "agent-system/policies/semantic-locks.json").read_text(encoding="utf-8")
    )
    sources = policy.get("interface_sources", [])
    match = next(
        (entry for entry in sources if entry.get("interface_id") == interface_id), None
    )
    if match is None:
        raise PlanningError("interface has no registered source", interface_id=interface_id)
    return _binding_from_source(
        root, interface_id, str(match["version"]), str(match["path"])
    )


def current_interface_binding(
    root: Path, interface_id: str, store: EventStore | None = None
) -> InterfaceBinding:
    if store is not None:
        rows = store.rows(
            "SELECT payload_json FROM artifacts WHERE artifact_id = ?",
            (f"interface-binding:{interface_id}",),
        )
        if rows:
            payload = json.loads(rows[0]["payload_json"])
            binding = _binding_from_source(
                root, interface_id, str(payload["version"]), str(payload["path"])
            )
            if binding.content_hash != payload.get("content_hash"):
                raise PlanningError(
                    "registered interface source changed without a control-plane bump",
                    interface_id=interface_id,
                )
            return binding
    return policy_interface_binding(root, interface_id)


def binding_from_source(
    root: Path, interface_id: str, version: str, relative_path: str
) -> InterfaceBinding:
    return _binding_from_source(root, interface_id, version, relative_path)
