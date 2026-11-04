"""Capability-based provider routing.

A task names a *capability class*; ``agent-system/routing/capability-registry.json``
is the only file that binds a class to a provider and model, and
``agent-system/routing/routing-policy.json`` supplies the floors. Routing is a pure
lookup plus cheap local availability facts. It never substitutes: a class bound to
a provider with no adapter is reported ``unsupported_provider`` with an actionable
message, and the caller refuses to start.

Statuses
--------
``available``            adapter exists and its executable is on PATH
``unsupported_provider`` the registry binds the class to a provider with no adapter
``executable_missing``   adapter exists, executable not found on PATH
``unauthenticated``      (``check_auth=True``) the CLI reports it is not logged in
``model_unavailable``    discovered only at invocation time (the account is not served
                         the registry model); never guessed here

Task-level outcomes add ``reviewer_unavailable`` (the policy-required reviewer class
cannot be routed, so the candidate could never be reviewed) and ``capability_floor``
(a hard floor of the routing policy is violated).

Reviewer selection rule (deterministic, documented)
---------------------------------------------------
``reviewer_matrix[task_class]`` is either a class name or prose. A class name is used
as is. For prose, the reviewer class is the registered class name that occurs
*earliest* in the text ("independent complex_implementation context or
work_package_planning specification review" -> ``complex_implementation``). Prose
naming no registered class (``architectural_authority``: "human approval ...") has no
machine reviewer and raises :class:`~agentctl.errors.HumanGate`. Because the policy
sets ``risk_may_raise_reviewer_floor``, a ``high``/``critical`` task never gets a
reviewer ranked below ``complex_implementation``. The policy's
``provider_diversity`` preference is advisory (``hard_requirement: false``) and is not
applied.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .adapters import ADAPTER_BINDINGS
from .adapters.claude_cli import child_sandbox, run_bounded
from .config import repo_root as resolve_repo_root
from .errors import HumanGate, PlanningError
from .scheduler import CAPABILITY_RANK

__all__ = [
    "AVAILABLE",
    "ROUTE_STATUSES",
    "RouteDecision",
    "TaskRoute",
    "load_registry",
    "reviewer_class_for",
    "route_capability",
    "route_reviewer",
    "route_task",
]

AVAILABLE = "available"
UNSUPPORTED_PROVIDER = "unsupported_provider"
EXECUTABLE_MISSING = "executable_missing"
UNAUTHENTICATED = "unauthenticated"
MODEL_UNAVAILABLE = "model_unavailable"
REVIEWER_UNAVAILABLE = "reviewer_unavailable"
CAPABILITY_FLOOR = "capability_floor"

ROUTE_STATUSES: tuple[str, ...] = (
    AVAILABLE,
    UNSUPPORTED_PROVIDER,
    EXECUTABLE_MISSING,
    UNAUTHENTICATED,
    MODEL_UNAVAILABLE,
)

_HIGH_RISK = frozenset({"high", "critical"})
_AUTH_PROBE_SECONDS = 20.0


@dataclass(frozen=True)
class RouteDecision:
    capability_class: str
    provider: str
    model_id: str
    model_version: str
    binding_type: str
    adapter: str | None
    status: str
    message: str
    executable: str | None = None

    @property
    def available(self) -> bool:
        return self.status == AVAILABLE

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaskRoute:
    """Everything routing knows about one task before any lease is issued."""

    task_id: str
    worker: RouteDecision
    reviewer: RouteDecision | None
    status: str
    message: str

    @property
    def available(self) -> bool:
        return self.status == AVAILABLE

    def to_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "message": self.message,
            "worker": self.worker.to_payload(),
            "reviewer": None if self.reviewer is None else self.reviewer.to_payload(),
        }


def _root(root: str | Path | None) -> Path:
    return Path(root).expanduser().resolve() if root is not None else resolve_repo_root()


def _load_json(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanningError(f"{description} cannot be loaded", path=str(path), error=str(exc)) from exc
    if not isinstance(value, dict):
        raise PlanningError(f"{description} root must be an object", path=str(path))
    return value


def load_registry(root: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Registry entries keyed by capability class (duplicates are a planning error)."""
    base = _root(root)
    document = _load_json(base / "agent-system/routing/capability-registry.json", "capability registry")
    entries: dict[str, dict[str, Any]] = {}
    for entry in document.get("entries", ()):
        name = str(entry["capability_class"])
        if name in entries:
            raise PlanningError("capability registry binds a class twice", capability_class=name)
        entries[name] = dict(entry)
    return entries


def _policy(root: Path) -> dict[str, Any]:
    return _load_json(root / "agent-system/routing/routing-policy.json", "routing policy")


def _logged_in(executable: str) -> bool | None:
    """``claude auth status`` -> loggedIn. Only that one boolean is retained; None = unknown."""
    with child_sandbox() as (env, cwd):  # the same environment the worker will get
        result = run_bounded(
            [executable, "auth", "status"], stdin=None, timeout=_AUTH_PROBE_SECONDS, env=env, cwd=cwd
        )
    if result.spawn_error is not None or result.timed_out:
        return None
    try:
        value = json.loads(result.stdout.decode("utf-8", errors="replace")).get("loggedIn")
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, bool) else None


def route_capability(
    capability_class: str,
    *,
    root: str | Path | None = None,
    check_auth: bool = False,
) -> RouteDecision:
    """Route one capability class through the registry. Never substitutes a provider."""
    entry = load_registry(root).get(capability_class)
    if entry is None:
        raise PlanningError(
            "capability class has no registry entry; routing cannot proceed",
            capability_class=capability_class,
        )
    provider = str(entry["provider"])
    common = dict(
        capability_class=capability_class,
        provider=provider,
        model_id=str(entry["model_id"]),
        model_version=str(entry["model_version"]),
        binding_type=str(entry["binding_type"]),
    )
    binding = ADAPTER_BINDINGS.get(provider)
    if binding is None:
        return RouteDecision(
            **common,
            adapter=None,
            status=UNSUPPORTED_PROVIDER,
            message=(
                f"{capability_class} is bound to provider {provider!r} "
                f"(model {common['model_id']!r}) and agentctl has no adapter for it. "
                "No other provider or model is substituted. Implement and exercise an "
                "adapter, or change the binding in agent-system/routing/capability-registry.json "
                "through the normal approval path."
            ),
        )
    executable = shutil.which(binding.executable)
    if executable is None:
        return RouteDecision(
            **common,
            adapter=binding.adapter_name,
            status=EXECUTABLE_MISSING,
            message=(
                f"{capability_class} routes to {provider!r} via {binding.adapter_name}, but "
                f"{binding.executable!r} is not on PATH. Install it or fix PATH; nothing is substituted."
            ),
        )
    if check_auth and _logged_in(executable) is False:
        return RouteDecision(
            **common,
            adapter=binding.adapter_name,
            status=UNAUTHENTICATED,
            message=(
                f"the {binding.executable!r} CLI reports it is not logged in. Authenticate it with its "
                "own login flow and rerun; agentctl never reads or passes credentials."
            ),
            executable=executable,
        )
    return RouteDecision(
        **common,
        adapter=binding.adapter_name,
        status=AVAILABLE,
        message=f"{capability_class} -> {provider}/{common['model_id']} via {binding.adapter_name}",
        executable=executable,
    )


def reviewer_class_for(
    task_class: str, *, risk: str | None = None, root: str | Path | None = None
) -> str:
    """Reviewer capability class for a task of ``task_class`` (see module docstring)."""
    base = _root(root)
    matrix = _policy(base).get("reviewer_matrix", {})
    value = matrix.get(task_class)
    if not isinstance(value, str):
        raise PlanningError("routing policy has no reviewer for this class", capability_class=task_class)
    known = tuple(load_registry(base))
    if value in known:
        chosen = value
    else:
        positions = [
            (match.start(), name)
            for name in known
            if (match := re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", value))
        ]
        if not positions:
            raise HumanGate(
                "the routing policy assigns no machine reviewer to this class",
                capability_class=task_class,
                policy=value,
            )
        chosen = min(positions)[1]
    floor = "complex_implementation"
    if risk in _HIGH_RISK and CAPABILITY_RANK.get(chosen, -1) < CAPABILITY_RANK[floor]:
        chosen = floor
    return chosen


def route_reviewer(
    task_class: str,
    *,
    risk: str | None = None,
    root: str | Path | None = None,
    check_auth: bool = False,
) -> RouteDecision:
    return route_capability(
        reviewer_class_for(task_class, risk=risk, root=root), root=root, check_auth=check_auth
    )


def _floor_violation(capability_class: str, risk: str, checks: int, root: Path) -> str | None:
    """Hard floors from the routing policy that can be decided from the task alone."""
    floors = _policy(root).get("hard_floors", {})
    if capability_class == "mechanical_execution" and floors.get(capability_class):
        if risk != "low":
            return (
                f"mechanical_execution is limited to {floors[capability_class]!r}; "
                f"this task's risk is {risk!r}. Re-class the task."
            )
        if checks < 1:
            return "mechanical_execution requires objective deterministic acceptance; the task declares none."
    return None


def route_task(
    task_id: str,
    *,
    store: Any | None = None,
    root: str | Path | None = None,
    check_auth: bool = False,
) -> TaskRoute:
    """Route a registered task: worker, policy-required reviewer, and floors.

    The result is ``available`` only if the worker class is routable, no hard floor is
    violated, **and** the policy-required reviewer class is routable. A task whose
    reviewer could never run must not be leased.
    """
    from .scheduler import Scheduler
    from .store import EventStore

    store = store or EventStore()
    base = _root(root if root is not None else store.repository_root)
    spec = Scheduler(store).task_spec(task_id)
    worker = route_capability(spec.capability_class, root=base, check_auth=check_auth)
    violation = _floor_violation(spec.capability_class, spec.risk, len(spec.deterministic_checks), base)
    try:
        reviewer = route_reviewer(spec.capability_class, risk=spec.risk, root=base, check_auth=False)
    except HumanGate as exc:
        return TaskRoute(task_id, worker, None, REVIEWER_UNAVAILABLE, str(exc))
    if not worker.available:
        return TaskRoute(task_id, worker, reviewer, worker.status, worker.message)
    if violation is not None:
        return TaskRoute(task_id, worker, reviewer, CAPABILITY_FLOOR, violation)
    if not reviewer.available:
        return TaskRoute(
            task_id,
            worker,
            reviewer,
            REVIEWER_UNAVAILABLE,
            f"the policy-required reviewer class {reviewer.capability_class!r} cannot be routed "
            f"({reviewer.status}): {reviewer.message} No lease is issued, because the candidate "
            "could never receive an independent review. Nothing is substituted.",
        )
    return TaskRoute(task_id, worker, reviewer, AVAILABLE, worker.message)
