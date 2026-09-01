"""Typed error hierarchy for agentctl.

One class per category in ``agent-system/policies/error-taxonomy.json`` plus four
mechanism errors the taxonomy does not name: ``SchemaUndeterminable``,
``UnsupportedKeyword``, ``IllegalTransition``, and ``NotImplementedAtLevel1``.

Every error carries a stable ``code`` string so structured output and the event
store can record *what kind* of failure occurred without parsing a message.
"""

from __future__ import annotations


class AgentctlError(Exception):
    """Base class for every error agentctl raises deliberately."""

    #: Stable machine-readable identifier. Overridden by every concrete subclass.
    code: str = "agentctl_error"

    def __init__(self, message: str = "", **context: object) -> None:
        super().__init__(message)
        self.message = message
        #: Optional structured detail (json pointer, path, offending value, ...).
        self.context: dict[str, object] = dict(context)

    def __str__(self) -> str:  # pragma: no cover - trivial
        if self.context:
            detail = ", ".join(f"{k}={v!r}" for k, v in sorted(self.context.items()))
            return f"{self.message} ({detail})" if self.message else detail
        return self.message


# --------------------------------------------------------------------------- #
# One class per error-taxonomy.json category.                                 #
# --------------------------------------------------------------------------- #

class PlanningError(AgentctlError):
    """Invalid DAG, incomplete contract, or missing gate."""

    code = "planning_error"


class ContractStale(AgentctlError):
    """A dependency, interface, or base commit changed under the contract."""

    code = "contract_stale"


class ImplementationFailure(AgentctlError):
    """Valid task, incorrect code."""

    code = "implementation_failure"


class VerificationFailure(AgentctlError):
    """A candidate fails an objective gate."""

    code = "verification_failure"


class OracleConflict(AgentctlError):
    """A test conflicts with an accepted requirement."""

    code = "oracle_conflict"


class PermissionViolation(AgentctlError):
    """A worker exceeded its files, tools, or actions."""

    code = "permission_violation"


class InfrastructureFailure(AgentctlError):
    """Toolchain, provider, simulator, disk, or network issue."""

    code = "infrastructure_failure"


class InvalidArtifact(AgentctlError):
    """Provider output fails schema, hash, or provenance validation."""

    code = "invalid_artifact"


class IntegrationConflict(AgentctlError):
    """A candidate conflicts after fan-in."""

    code = "integration_conflict"


class BudgetExhausted(AgentctlError):
    """A cost, token, attempt, or time limit was reached."""

    code = "budget_exhausted"


class NoProgress(AgentctlError):
    """Repeated failure without a meaningful delta."""

    code = "no_progress"


class HumanGate(AgentctlError):
    """A required human authority is unavailable; remain blocked, no workaround."""

    code = "human_gate"


#: category string -> error class, for taxonomy-driven construction.
TAXONOMY: dict[str, type[AgentctlError]] = {
    cls.code: cls
    for cls in (
        PlanningError,
        ContractStale,
        ImplementationFailure,
        VerificationFailure,
        OracleConflict,
        PermissionViolation,
        InfrastructureFailure,
        InvalidArtifact,
        IntegrationConflict,
        BudgetExhausted,
        NoProgress,
        HumanGate,
    )
}


# --------------------------------------------------------------------------- #
# Mechanism errors not in the taxonomy.                                       #
# --------------------------------------------------------------------------- #

class SchemaUndeterminable(InvalidArtifact):
    """An artifact path carries no ``artifact_type``, or names an unknown type.

    A subtype of :class:`InvalidArtifact`: an artifact whose schema cannot be
    chosen is, by definition, an artifact that failed validation.
    """

    code = "schema_undeterminable"


class UnsupportedKeyword(AgentctlError):
    """A schema uses a keyword outside the ADR-0003 bounded subset.

    Never swallowed: a validator that silently ignores a constraint produces
    confident green checks over unvalidated data.
    """

    code = "unsupported_keyword"


class IllegalTransition(AgentctlError):
    """A ``(state, event)`` pair absent from the scheduler transition table."""

    code = "illegal_transition"


class NotImplementedAtLevel1(AgentctlError):
    """A CLI surface fixed at P0 whose behavior is deferred to P1 entry.

    The command exists so the surface and adapter protocol are frozen for
    Level-3 forward compatibility; calling it raises this rather than pretending
    to act.
    """

    code = "not_implemented_at_level_1"


class ConfigError(AgentctlError):
    """agentctl cannot resolve a safe runtime configuration."""

    code = "config_error"
