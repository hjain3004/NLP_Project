"""Validate forward work plans and materialize schedulable task state."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .config import repo_root as resolve_repo_root
from .context import ContextCompiler
from .dag import DependencyGraph
from .errors import PlanningError
from .locks import semantic_lock_claims
from .scheduler import Scheduler, TaskSpec
from .store import EventStore
from .validate import validate_artifact

__all__ = ["PlanLoader", "task_spec_from_contract"]


def _interface_pins(values: object) -> dict[str, str]:
    pins: dict[str, str] = {}
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, str) or "@" not in value:
            raise PlanningError("interface pin is malformed", value=value)
        interface_id, version = value.rsplit("@", 1)
        pins[interface_id] = version
    return pins


def task_spec_from_contract(
    contract: Mapping[str, Any], *, context_hash: str, repository_root: str | Path | None = None
) -> TaskSpec:
    identity = contract["identity"]
    scope = contract["scope"]
    routing = contract["routing"]
    permissions = contract["permissions"]
    budgets = contract["budgets"]
    dependencies = contract["dependencies"]
    contract_hash = str(identity["contract_hash"])
    return TaskSpec(
        task_id=str(identity["task_id"]),
        work_package_id=str(identity["work_package_id"]),
        contract_hash=contract_hash,
        context_hash=context_hash,
        base_commit=str(identity["base_commit"]),
        capability_class=str(routing["capability_class"]),
        risk=str(routing["risk"]),
        enforcement_class=str(permissions["enforcement_class"]),
        writable_paths=tuple(scope["writable_paths"]),
        prohibited_paths=tuple(scope["prohibited_paths"]),
        semantic_locks=semantic_lock_claims(
            scope["semantic_locks"], repository_root or resolve_repo_root()
        ),
        maximum_attempts=int(budgets["maximum_attempts"]),
        maximum_artifact_corrections=int(budgets["maximum_artifact_corrections"]),
        dependencies=tuple(dependencies["requires"]),
        interface_pins=_interface_pins(dependencies["consumes"]),
        deterministic_checks=tuple(contract["verification"]["deterministic_checks"]),
        human_approval_ref=permissions.get("human_approval_ref"),
        human_approval_action=permissions.get("required_human_approval_action"),
        integration_order=dependencies.get("integration_order"),
    )


class PlanLoader:
    """Load schema-valid forward contracts into one EventStore."""

    def __init__(
        self,
        repository_root: str | Path | None = None,
        store: EventStore | None = None,
    ) -> None:
        self.root = (
            Path(repository_root).expanduser().resolve()
            if repository_root is not None
            else resolve_repo_root()
        )
        self.store = store or EventStore()
        self.scheduler = Scheduler(self.store)

    def validate(self, work_package_path: str | Path) -> dict[str, Any]:
        path = Path(work_package_path).expanduser().resolve()
        work_package = validate_artifact(path, repository_root=self.root)
        if work_package.get("artifact_type") != "work-package":
            raise PlanningError("plan root is not a work package", path=str(path))
        task_paths = sorted((path.parent / "tasks").glob("*.json"))
        if not task_paths:
            if work_package.get("bootstrap_phase") is True and work_package.get("status") == "complete":
                return {
                    "work_package_id": str(work_package["work_package_id"]),
                    "tasks": [],
                    "status": "validated_bootstrap_record",
                }
            raise PlanningError("work package has no forward task contracts", path=str(path))
        contracts = [validate_artifact(item, repository_root=self.root) for item in task_paths]
        work_package_id = str(work_package["work_package_id"])
        for contract in contracts:
            if contract["identity"]["work_package_id"] != work_package_id:
                raise PlanningError(
                    "task belongs to another work package",
                    task_id=contract["identity"]["task_id"],
                )
            if contract["identity"]["execution_mode"] != "control_plane":
                raise PlanningError(
                    "only forward control-plane contracts are schedulable",
                    task_id=contract["identity"]["task_id"],
                )
        graph = DependencyGraph(
            {
                str(contract["identity"]["task_id"]): tuple(
                    contract["dependencies"]["requires"]
                )
                for contract in contracts
            }
        )
        graph.topological_order()
        compiler = ContextCompiler(self.root)
        loaded: list[dict[str, str]] = []
        by_id = {str(item["identity"]["task_id"]): item for item in contracts}
        for task_id in graph.topological_order():
            contract = by_id[task_id]
            role = str(contract["routing"]["capability_class"])
            manifest = compiler.compile(
                task_id=task_id,
                attempt_id=f"{task_id}-attempt-1",
                role=role,
                inputs=compiler.default_inputs(task_id, role),
                compiled_at=str(contract["identity"]["created_at"]),
            )
            spec = task_spec_from_contract(
                contract, context_hash=manifest["context_hash"], repository_root=self.root
            )
            self.scheduler.register_task(spec)
            loaded.append({"task_id": task_id, "context_hash": manifest["context_hash"]})
        return {"work_package_id": work_package_id, "tasks": loaded, "status": "validated"}
