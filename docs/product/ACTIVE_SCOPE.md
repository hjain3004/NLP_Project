# CashBooks Active Scope

**Status:** Active product scope; Milestone 0 changes approved by owner merge.
**Written:** P0 Task 5, 2026-09-01.

## What is authorized

One program: the **headless local-intelligence engine**, built through the milestones in
`docs/ENGINE_BUILD_PLAN.md`, ending at G3-B with the additive G3-L live-model claim.

Nothing beyond G3 is authorized by this document. The user experience, the recruiter
release, Plaid, a backend, a Share Extension, and broader accounting each require a new
product decision, a written design, an implementation plan, and human approval.

## What the engine does

It takes a local PDF and produces a persisted, evidence-backed ledger record — with a
deliberate human confirmation in the middle, and nothing written before that confirmation.

```text
local PDF
→ PDFKit/Vision extraction
→ typed untrusted draft through rule-based or Foundation Models adapters
→ deterministic validation, risk, and evidence
→ immutable review snapshot
→ deliberate external confirmation command
→ atomic SwiftData persistence
→ immutable result snapshot
```

Every stage is testable without a user interface and without a live model. That is the point
of stopping here: the engine's real latency, real failure modes, and real confidence
behaviour become observable *before* anyone designs screens around imagined versions of them.

## Platforms

**iOS** is the eventual product target and a platform-integration host.

**macOS** is the live Foundation Models validation harness and a second integration host. It
exists because the development machine can run the model and the phone in question cannot.

**visionOS is removed from the milestone-one support matrix.** The generated Xcode project
lists it; app-shell changes are deferred by the engine build plan. It may be reconsidered
only after the recruiter release, and only with an explicit product reason. Carrying a third
platform through the engine milestones costs build matrix, test matrix, and review attention,
and buys nothing the milestone needs.

The current milestone does **not** claim a complete user product on either platform. It
proves the local engine behind the future iOS experience.

## What "done" means at each gate

The implementation milestones and their demonstrations are defined in
`docs/ENGINE_BUILD_PLAN.md`.

- **G3-B** — the full headless pipeline runs on synthetic fixtures, with persistence,
  cancellation, privacy, and fallback all evidenced, and Foundation Models integration tested
  against fakes.
- **G3-L** — an additive claim: live Foundation Models compatibility, evidenced on a declared
  macOS configuration. G3-B does not depend on it, and it may remain open indefinitely
  without invalidating anything.

## Claims discipline

What may be said about this project is bounded by gate, and the boundary is not a formality —
it is the difference between a defensible engineering claim and an overclaim a reviewer can
puncture in one question.

At G3-B: a headless local-intelligence engine is implemented and deterministically tested,
including a Foundation Models adapter tested against fake sessions. Not that live Foundation
Models is supported. Not that a finished app exists.

At G3-L: live Foundation Models compatibility is evidenced on the named configuration, with
its limitations stated.

This claims discipline binds every README line, resume line, demo script, and conversation.

## Related

- `docs/product/NON_GOALS.md` — what is explicitly excluded
- `docs/product/HERO_SCENARIO.md` — the single story the engine must satisfy
- `docs/security/INVARIANTS.md` — the twenty-six release-blocking properties
- `docs/DOCUMENT_AUTHORITY.md` — precedence when documents disagree
