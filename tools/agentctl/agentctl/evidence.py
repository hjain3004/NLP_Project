"""Export and replay privacy-safe event evidence."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .events import validate_event
from .hashing import canonical_json, sha256_of_json
from .store import EventStore

__all__ = [
    "export_work_package_events",
    "verify_dry_run_semantics",
    "verify_event_export",
]


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


def verify_dry_run_semantics(
    source: str | Path,
    *,
    repository_root: str | Path,
    require_committed: bool = False,
) -> dict[str, Any]:
    """Replay the DRY-RUN state machine against committed task contracts."""
    root = Path(repository_root).resolve()
    path = Path(source).resolve()
    document = verify_event_export(path, repository_root=root)
    if document.get("work_package_id") != "DRY-RUN":
        raise ValueError("dry-run evidence names another work package")
    if require_committed:
        relative = path.relative_to(root).as_posix()
        committed = subprocess.run(
            ["git", "show", f"HEAD:{relative}"],
            cwd=root,
            capture_output=True,
            check=False,
        )
        if committed.returncode != 0 or committed.stdout != path.read_bytes():
            raise ValueError("dry-run evidence is not the exact blob at evaluated HEAD")

    contracts: dict[str, dict[str, Any]] = {}
    for contract_path in sorted((root / "agent-system/work/DRY-RUN/tasks").glob("*.json")):
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contracts[str(contract["identity"]["task_id"])] = contract
    if not contracts:
        raise ValueError("dry-run task contracts are absent")

    states: dict[str, str] = {}
    required: set[str] = set()
    state_events = {
        "lease_issued": {("ready", "leased")},
        "worker_acknowledged": {("leased", "running")},
        "lease_expired": {("leased", "ready"), ("running", "ready")},
        "candidate_ingested": {("running", "candidate")},
        "verification_started": {("candidate", "deterministic_verification")},
        "checks_passed": {("deterministic_verification", "independent_review")},
        "review_passed": {("independent_review", "integration_ready")},
        "dependency_changed": {
            ("ready", "stale"),
            ("validated", "stale"),
            ("leased", "stale"),
            ("running", "stale"),
        },
        "context_rebuilt": {("stale", "validated")},
    }
    same_state_events = {
        "admission_rejected",
        "late_artifact",
        "review_pack_prepared",
        "review_rejected",
        "review_assigned",
    }
    for event in document["events"]:
        event_type = str(event["event_type"])
        reason = str(event["reason_code"])
        task_id = event.get("task_id")
        if event_type == "task_registered":
            if task_id not in contracts or event.get("next_state") != "ready":
                raise ValueError("task registration does not bind a DRY-RUN contract")
            expected_hash = contracts[str(task_id)]["identity"]["contract_hash"]
            if event.get("contract_hash") != expected_hash:
                raise ValueError("task registration contract hash changed")
            states[str(task_id)] = "ready"
            continue
        if event_type == "ready_set_computed":
            refs = event.get("artifact_refs", [])
            if reason != "multiple_ready_tasks" or len(refs) < 2:
                raise ValueError("ready-set evidence does not contain several tasks")
            if any(ref not in contracts or states.get(ref) != "ready" for ref in refs):
                raise ValueError("ready-set evidence names a non-ready task")
            required.add(reason)
            continue
        if not isinstance(task_id, str) or task_id not in contracts:
            raise ValueError("stateful dry-run event names an unknown task")
        prior = event.get("prior_state")
        next_state = event.get("next_state")
        if states.get(task_id) != prior:
            raise ValueError(
                f"dry-run state replay diverged for {task_id}: "
                f"expected {states.get(task_id)!r}, event says {prior!r}"
            )
        if event_type in state_events:
            if (prior, next_state) not in state_events[event_type]:
                raise ValueError(f"illegal dry-run transition for {event_type}")
            states[task_id] = str(next_state)
        elif event_type in same_state_events:
            if prior != next_state:
                raise ValueError(f"same-state event {event_type} changed task state")
        else:
            raise ValueError(f"unsupported dry-run event type: {event_type}")
        if event_type == "candidate_ingested" and len(event.get("artifact_refs", [])) < 2:
            raise ValueError("candidate event lacks manifest and trusted Git binding refs")
        if event_type == "checks_passed" and not event.get("artifact_refs"):
            raise ValueError("verification event lacks trusted evidence reference")
        if event_type == "review_rejected" and reason == "self_review_rejected":
            refs = event.get("artifact_refs", [])
            if not any(str(ref).startswith("collision:") for ref in refs):
                raise ValueError("self-review event lacks the collided identity dimension")
        if event_type == "dependency_changed" and reason == "context_stale":
            refs = event.get("artifact_refs", [])
            if not any(re.fullmatch(r"old:[0-9a-f]{64}", str(ref)) for ref in refs):
                raise ValueError("context-stale event lacks old interface hash")
            if not any(re.fullmatch(r"new:[0-9a-f]{64}", str(ref)) for ref in refs):
                raise ValueError("context-stale event lacks new interface hash")
        required.add(reason)

    expected_reasons = {
        "multiple_ready_tasks",
        "path_lock_conflict",
        "semantic_lock_conflict",
        "stale_fencing_token",
        "self_review_rejected",
        "context_stale",
        "context_rebuilt",
        "verification_passed",
    }
    missing = sorted(expected_reasons - required)
    if missing:
        raise ValueError(f"dry-run semantic evidence is incomplete: {missing}")
    return document
