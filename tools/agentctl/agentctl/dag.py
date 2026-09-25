"""Deterministic dependency graphs and transitive invalidation."""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Mapping

from .errors import PlanningError

__all__ = ["CycleDetected", "DependencyGraph"]


class CycleDetected(PlanningError):
    """A dependency cycle, carrying the exact cycle nodes in traversal order."""

    code = "dependency_cycle"

    def __init__(self, nodes: Iterable[str]) -> None:
        self.nodes = tuple(nodes)
        super().__init__("dependency graph contains a cycle", nodes=list(self.nodes))


class DependencyGraph:
    """A graph represented as ``node -> direct dependencies``."""

    def __init__(self, dependencies: Mapping[str, Iterable[str]]) -> None:
        nodes = set(dependencies)
        for required in dependencies.values():
            nodes.update(required)
        self.dependencies: dict[str, frozenset[str]] = {
            node: frozenset(dependencies.get(node, ())) for node in sorted(nodes)
        }
        dependents: dict[str, set[str]] = {node: set() for node in nodes}
        for node, required in self.dependencies.items():
            for dependency in required:
                dependents[dependency].add(node)
        self.dependents: dict[str, frozenset[str]] = {
            node: frozenset(children) for node, children in dependents.items()
        }

    @property
    def nodes(self) -> frozenset[str]:
        return frozenset(self.dependencies)

    def find_cycle(self) -> list[str]:
        """Return one exact cycle, or an empty list when the graph is acyclic."""
        state: dict[str, int] = {node: 0 for node in self.dependencies}
        stack: list[str] = []
        stack_positions: dict[str, int] = {}

        def visit(node: str) -> list[str] | None:
            state[node] = 1
            stack_positions[node] = len(stack)
            stack.append(node)
            for dependency in sorted(self.dependencies[node]):
                if state[dependency] == 0:
                    found = visit(dependency)
                    if found is not None:
                        return found
                elif state[dependency] == 1:
                    return stack[stack_positions[dependency] :].copy()
            stack.pop()
            stack_positions.pop(node)
            state[node] = 2
            return None

        for node in sorted(self.dependencies):
            if state[node] == 0:
                found = visit(node)
                if found is not None:
                    return found
        return []

    def topological_order(self) -> list[str]:
        """Return a stable dependency-first order or raise ``CycleDetected``."""
        cycle = self.find_cycle()
        if cycle:
            raise CycleDetected(cycle)

        remaining = {node: len(required) for node, required in self.dependencies.items()}
        ready = [node for node, count in remaining.items() if count == 0]
        heapq.heapify(ready)
        ordered: list[str] = []
        while ready:
            node = heapq.heappop(ready)
            ordered.append(node)
            for dependent in sorted(self.dependents[node]):
                remaining[dependent] -= 1
                if remaining[dependent] == 0:
                    heapq.heappush(ready, dependent)
        return ordered

    def descendants(self, node: str) -> set[str]:
        """Return every direct and indirect dependent of ``node``."""
        if node not in self.dependencies:
            raise KeyError(node)
        found: set[str] = set()
        pending = list(self.dependents[node])
        while pending:
            current = pending.pop()
            if current in found:
                continue
            found.add(current)
            pending.extend(self.dependents[current])
        return found

    def invalidation_closure(self, changed_nodes: Iterable[str]) -> set[str]:
        """Include changed nodes and every transitive dependent."""
        invalidated: set[str] = set()
        for node in changed_nodes:
            if node not in self.dependencies:
                raise KeyError(node)
            invalidated.add(node)
            invalidated.update(self.descendants(node))
        return invalidated

    def stale_for_interface_change(
        self,
        interface_id: str,
        current_hash: str,
        pins: Mapping[str, Mapping[str, str]],
    ) -> set[str]:
        """Invalidate mismatched consumers and their full dependent closure."""
        consumers = {
            task_id
            for task_id, task_pins in pins.items()
            if interface_id in task_pins and task_pins[interface_id] != current_hash
        }
        unknown = consumers - self.nodes
        if unknown:
            raise PlanningError(
                "interface pins name tasks outside the dependency graph",
                tasks=sorted(unknown),
            )
        return self.invalidation_closure(consumers)
