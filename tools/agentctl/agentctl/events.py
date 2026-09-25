"""Event construction and privacy-safe validation.

Events are concise operational records, never a place to persist source
documents, prompts, hidden reasoning, or raw PII. Privacy checks run before
schema checks so a denylisted field is reported as the reason for rejection
even though ``additionalProperties: false`` would also reject it.
"""

from __future__ import annotations

import datetime as _datetime
import json
import re
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .config import repo_root as resolve_repo_root
from .errors import InvalidArtifact
from .jsonschema_subset import validate as validate_schema

__all__ = ["InvalidEvent", "make_event", "validate_event"]


class InvalidEvent(InvalidArtifact):
    """An event violates its schema or the telemetry privacy boundary."""

    code = "invalid_event"


_DENIED_KEY_PARTS = frozenset(
    {"text", "content", "body", "transcript", "reasoning", "raw", "pii", "prompt"}
)
_KEY_PARTS = re.compile(r"[^a-z0-9]+")
_MAX_STRING_BYTES = 2048


def _key_is_denied(key: str) -> bool:
    parts = {part for part in _KEY_PARTS.split(key.lower()) if part}
    return bool(parts & _DENIED_KEY_PARTS)


def _walk_privacy(value: Any, pointer: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_pointer = f"{pointer}/{key}"
            if _key_is_denied(str(key)):
                raise InvalidEvent(
                    "event contains a denylisted telemetry key",
                    json_pointer=child_pointer,
                    key=key,
                )
            _walk_privacy(child, child_pointer)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_privacy(child, f"{pointer}/{index}")
    elif isinstance(value, str):
        byte_count = len(value.encode("utf-8"))
        if byte_count > _MAX_STRING_BYTES:
            raise InvalidEvent(
                "event string exceeds the 2048-byte telemetry limit",
                json_pointer=pointer,
                bytes=byte_count,
            )


def validate_event(
    event: Mapping[str, Any], *, repository_root: str | Path | None = None
) -> Mapping[str, Any]:
    """Validate event privacy and schema conformance, returning ``event``."""
    if not isinstance(event, Mapping):
        raise InvalidEvent("event must be a JSON object")
    _walk_privacy(event)

    root = (
        Path(repository_root).expanduser().resolve()
        if repository_root
        else resolve_repo_root()
    )
    schema_path = root / "agent-system" / "schemas" / "event.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvalidEvent(
            "event schema cannot be loaded", schema=str(schema_path), error=str(exc)
        ) from exc

    ok, errors = validate_schema(event, schema)
    if not ok:
        pointer, keyword, message = errors[0]
        raise InvalidEvent(
            "event does not conform to event.schema.json",
            json_pointer=pointer,
            keyword=keyword,
            detail=message,
            errors=errors,
        )
    return event


def make_event(
    *,
    event_type: str,
    reason_code: str,
    actor_role: str,
    event_id: str | None = None,
    trace_id: str | None = None,
    occurred_at: str | None = None,
    task_id: str | None = None,
    attempt_id: str | None = None,
    work_package_id: str | None = None,
    phase_id: str | None = None,
    provider: str | None = None,
    model_version: str | None = None,
    prior_state: str | None = None,
    next_state: str | None = None,
    contract_hash: str | None = None,
    context_hash: str | None = None,
    repository_commits: list[str] | None = None,
    artifact_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Build a schema-shaped event with generated IDs and an RFC 3339 time."""
    timestamp = occurred_at or (
        _datetime.datetime.now(_datetime.timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    event: dict[str, Any] = {
        "artifact_type": "event",
        "event_id": event_id or str(uuid.uuid4()),
        "trace_id": trace_id or str(uuid.uuid4()),
        "occurred_at": timestamp,
        "actor_role": actor_role,
        "repository_commits": list(repository_commits or []),
        "artifact_refs": list(artifact_refs or []),
        "reason_code": reason_code,
        "event_type": event_type,
    }
    optional = {
        "task_id": task_id,
        "attempt_id": attempt_id,
        "work_package_id": work_package_id,
        "phase_id": phase_id,
        "provider": provider,
        "model_version": model_version,
        "prior_state": prior_state,
        "next_state": next_state,
        "contract_hash": contract_hash,
        "context_hash": context_hash,
    }
    event.update({key: value for key, value in optional.items() if value is not None})
    return event
