# CashBooks Engineering Roadmap

**Status:** Active program map. P0 bootstrap in progress.
**Authority:** This document summarizes the authorized program; `docs/product/ACTIVE_SCOPE.md`
and `docs/DOCUMENT_AUTHORITY.md` govern if a summary here is incomplete.

## Immediate delivery target — 2026-10-22

The owner has requested automated model routing and candidate integration by the end of
2026-10-22 in America/Los_Angeles. This is a delivery target, not a completion claim or a
guaranteed estimate. The infrastructure work is prioritized before application development;
the owner must finish Swift MCP setup before Swift development starts.

The executable outcome and bounded work sequence are in
[`AUTOMATION_DELIVERY.md`](AUTOMATION_DELIVERY.md). The earlier deferral of routing and
integration is superseded as a delivery priority by this request. Existing invariants,
typed approval requirements, review independence, and phase-gate status are unchanged.
G0 remains open until its actual closure requirements are met; delivering automation is
not synonymous with closing G0. Do not expand this delivery into unrelated P0 cleanup.

Report working behavior and remaining gaps, not test totals as a proxy for completion.
Routing and integration are not complete until the documented live cycle succeeds.

## Authorized sequence

| Phase | Outcome | Gate | State and contract timing |
|---|---|---|---|
| P0 | Multi-model control plane | G0 | Active; bootstrap construction record only |
| P1 | Repository, naming, and build foundation | G1 | Planned; contracts materialized at G0 |
| P2 | Contract and oracle freeze | G2 | Planned; contracts materialized at G1 |
| P3 | Parallel subsystem implementation | G3-B | Planned; contracts materialized at G2 |
| P3 additive claim | Live Foundation Models compatibility | G3-L | Open; requires separate live evidence |

P0 through G3-B are the only authorized implementation program. G3-L is an additive
evidence claim and is not required to close G3-B.

## Deferred programs

| Program | Earliest gate | State | Requirement before work starts |
|---|---|---|---|
| UI product program | G4 | Deferred | New brainstorm, written design, plan, and human approval after G3-B |
| Recruiter-release program | G5 | Deferred; task IDs unassigned | Separate release plan and human approval |
| Plaid | Unassigned | Deferred | New product decision |
| Backend or network sync | Unassigned | Deferred | New product and privacy decision |
| Share Extension | Unassigned | Deferred | New acquisition-boundary decision |
| Broader accounting | Unassigned | Deferred | New product-scope decision |

## Claims discipline

At G0, it is accurate to say that an engineering control plane exists. Nothing about the
CashBooks product works yet.

At G3-B, it is accurate to say that a headless local-intelligence engine is implemented and
deterministically tested, and that the Foundation Models integration is implemented and
tested against fake sessions. Not that live Foundation Models is supported; that claim is
reserved for G3-L. Not that a finished app or recruiter demo exists; that belongs to the
separately authorized recruiter-release program ending at G5.

At G3-L, live Foundation Models compatibility may be claimed only for the declared macOS,
hardware, OS, Xcode, language, region, and Apple Intelligence configuration, with observed
limitations named alongside the claim.

Every README line, resume line, demo script, and recruiter conversation is bound by this
section.

## Sources of detail

- `docs/program/PROGRAM_DAG.md` records dependencies and permitted concurrency.
- `docs/program/PHASE_GATES.md` is generated from machine-readable gate criteria.
- `agent-system/work/program-dag.json` is the machine-readable program topology.
- Later-phase criteria are written at the preceding gate against real repository state; they
  are not predeclared during P0.
