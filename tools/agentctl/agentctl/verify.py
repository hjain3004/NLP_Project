"""Trusted deterministic candidate verification."""

from .scheduler import Scheduler
from .store import EventStore


def verify_task(task_id: str, *, store: EventStore | None = None) -> dict:
    return Scheduler(store or EventStore()).verify_candidate(task_id)
