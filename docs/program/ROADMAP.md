# CashBooks Engineering Roadmap

**Status:** Active program map. P0 bootstrap in progress.
**Authority:** This document summarizes the authorized program; `docs/product/ACTIVE_SCOPE.md`
and `docs/DOCUMENT_AUTHORITY.md` govern if a summary here is incomplete.

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
