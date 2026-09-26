"""Level-1 placeholder for the P1 verification runner."""

from .errors import NotImplementedAtLevel1


def verify_task(task_id: str) -> None:
    raise NotImplementedAtLevel1(
        "deterministic candidate verification lands at P1 entry", task_id=task_id
    )
