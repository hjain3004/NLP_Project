"""Level-1 placeholder for the P1 integration queue."""

from .errors import NotImplementedAtLevel1


def integrate_task(task_id: str) -> None:
    raise NotImplementedAtLevel1(
        "candidate integration lands at P1 entry", task_id=task_id
    )
