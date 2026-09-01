"""agentctl — the CashBooks development-only control-plane CLI.

Automation Level 1. This package owns task state, dependency resolution, path and
semantic locks, leases and fencing tokens, context compilation, reviewer
independence, human-approval verification, gate evaluation, and the append-only
event store. Models produce typed artifacts; only agentctl changes state.

It is never linked into, imported by, or shipped with the CashBooks application.

Task 9 builds layer A only: canonical hashing, the bounded JSON Schema subset
validator, typed errors, config resolution, artifact models, the thin CLI
dispatcher, and the provider-neutral adapter protocol. Layers B-G arrive in
Tasks 12-17; layer H (integration and verification) is deferred to P1 entry and
ships as declared stubs.
"""

__all__ = ["__version__"]

# Mirrors agent-system/VERSION, the artifact-protocol version. Bumping it is a
# schema commitment and requires human approval (see agent-system/README.md).
__version__ = "0.1.0"
