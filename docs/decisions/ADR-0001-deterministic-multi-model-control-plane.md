# ADR-0001 — Deterministic multi-model control plane

## ID and title

ADR-0001 — Deterministic multi-model control plane

## Status

status: superseded by ADR-0006

Accepted 2026-08-31 with the canonical engineering design; recorded here 2026-09-01.

## Context

CashBooks is built by a solo developer directing model workers across several providers. The
work has genuine internal independence — deterministic risk logic, PDF acquisition, semantic
extraction, fixtures, persistence, and adapter research do not need each other — but it also
has shared surfaces that conflict without touching the same file: DTOs, project
configuration, package manifests, SwiftData schemas, financial invariants, workflow state.

Model budget is finite and has already run out once mid-project. Correctness matters more
than throughput: this is financial software whose entire value proposition is that it does
not silently write the wrong number.

## Considered options

**A free-form swarm.** Agents delegate and converse until they believe the objective is met.
Flexible, low up-front design, visually impressive. Rejected: authority becomes ambiguous,
stop conditions are unreliable, duplicate work and circular reviews are common, chat history
becomes accidental state, cost is unbounded, and a persuasive model can mask weak evidence.

**A fixed assembly line.** Every task passes through the same planner → implementer →
reviewer → merger sequence. Simple, predictable, auditable. Rejected as the sole model: it
serializes independent work, routes trivial changes through expensive stages, handles
cross-cutting failures poorly, and cannot adapt to task-specific risk.

**A deterministic DAG with bounded specialists.** Code owns state and authority; models
operate only inside typed work packets.

## Decision

Adopt the deterministic DAG with bounded specialists.

The control plane owns task state, dependency resolution, path and semantic locks, leases and
concurrency, budgets, verification execution, reviewer independence, integration eligibility,
durable event history, and human approval gates. It is not itself an LLM and never defers a
state transition or permission decision to one.

Models propose plans, implement scoped changes, diagnose evidence, review candidates, and
prepare integration artifacts. They may not grant themselves permissions, change scheduler
state, approve their own work, waive a failed test, or merge without evidence.

The governing rule: **parallelize implementation behind frozen interfaces; never parallelize
interface invention.**

## Consequences

More up-front contract and tooling work before any application code is written — P0 exists
entirely because of this decision. Semantic locks require deliberate maintenance. Task authors
must define objective acceptance evidence rather than describing intent.

In exchange: safe concurrency, measurable routing, resumability across sessions and providers,
vendor neutrality, explicit cost control, and a clean separation between stochastic generation
and deterministic authority.

The single integration queue is a deliberate bottleneck. For a project this size, deterministic
integration is worth more than maximum throughput.

## Affected contracts and locks

`policy:agent-constitution-v1`, `schema:agent-artifacts-v1`, `routing:capability-registry-v1`.
Establishes every artifact schema and the task state machine.

## Supersedes

The single-agent orchestration model in
`docs/superpowers/specs/2026-08-30-cashbooks-control-plane-design.md`, and the serial execution
authority of `docs/superpowers/plans/2026-08-31-cashbooks-control-plane.md`. Both retain their
product decisions and acceptance criteria.

## Required approvals

Human approval of the canonical design, 2026-08-31. G0 scope approval re-confirms it.

## References

- `docs/superpowers/specs/2026-08-31-cashbooks-multi-model-agent-system-design.md` §7, §9, §59
- `docs/superpowers/reviews/2026-08-31-cashbooks-multi-model-agent-system-self-review.md`
