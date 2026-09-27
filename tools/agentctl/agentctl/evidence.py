"""Export and replay privacy-safe event evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .events import validate_event
from .hashing import canonical_json, sha256_of_json
from .store import EventStore

__all__ = ["export_work_package_events", "verify_event_export"]


def _chain(events: list[dict[str, Any]]) -> tuple[list[str], str]:
    previous = "0" * 64
    links: list[str] = []
    for event in events:
        event_hash = sha256_of_json(event)
        previous = hashlib.sha256(f"{previous}:{event_hash}".encode("ascii")).hexdigest()
        links.append(previous)
    return links, previous


def export_work_package_events(
    store: EventStore, work_package_id: str, destination: str | Path
) -> dict[str, Any]:
    task_ids = [
        row["task_id"]
        for row in store.rows(
            "SELECT task_id FROM tasks WHERE work_package_id = ? ORDER BY task_id",
            (work_package_id,),
        )
    ]
    clauses = ["work_package_id = ?"]
    parameters: list[str] = [work_package_id]
    if task_ids:
        clauses.append(f"task_id IN ({','.join('?' for _ in task_ids)})")
        parameters.extend(task_ids)
    rows = store.rows(
        f"SELECT sequence, payload_json FROM events WHERE {' OR '.join(clauses)} ORDER BY sequence",
        parameters,
    )
    events = [json.loads(row["payload_json"]) for row in rows]
    links, chain_hash = _chain(events)
    document = {
        "artifact_type": "event-export",
        "work_package_id": work_package_id,
        "event_count": len(events),
        "source_sequences": [int(row["sequence"]) for row in rows],
        "chain_links": links,
        "chain_hash": chain_hash,
        "events": events,
    }
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return document


def verify_event_export(
    source: str | Path, *, repository_root: str | Path
) -> dict[str, Any]:
    path = Path(source)
    document = json.loads(path.read_text(encoding="utf-8"))
    events = document.get("events")
    if document.get("artifact_type") != "event-export" or not isinstance(events, list):
        raise ValueError("not an event-export artifact")
    if document.get("event_count") != len(events):
        raise ValueError("event count does not match")
    identifiers: set[str] = set()
    for event in events:
        validate_event(event, repository_root=repository_root)
        if event["event_id"] in identifiers:
            raise ValueError("event export repeats an event identifier")
        identifiers.add(event["event_id"])
    links, chain_hash = _chain(events)
    if links != document.get("chain_links") or chain_hash != document.get("chain_hash"):
        raise ValueError("event export chain does not verify")
    sequences = document.get("source_sequences")
    if not isinstance(sequences, list) or sequences != sorted(sequences) or len(sequences) != len(events):
        raise ValueError("event source sequence is not ordered")
    return document
