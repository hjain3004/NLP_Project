"""Level-1 placeholder for automated provider routing."""

from .errors import NotImplementedAtLevel1


def route_task(task_id: str) -> None:
    raise NotImplementedAtLevel1(
        "automated provider routing lands after manual Level 1", task_id=task_id
    )
