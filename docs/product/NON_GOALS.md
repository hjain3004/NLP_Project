# CashBooks Non-Goals

**Status:** Level 1 authority. Human-approved at G0.
**Written:** P0 Task 5, 2026-09-01.
**Approval binding:** the SHA-256 of this file is sealed by the G0 scope approval.

These are not "later" in the sense of "soon" or "if there's time". Each is **blocked** for the
authorized program, and unblocking one requires a new product decision, a written design, an
implementation plan, and human approval — not a worker deciding it would be easy.

A task contract that requires any of these is mis-planned. Say so and stop.

## Blocked for the active program

- SwiftUI screens and navigation
- design tokens
- animation
- UI accessibility presentation
- UI automation
- Plaid
- backend or network sync
- Share Extension
- standalone image or camera ingestion
- broader accounting workflows
- App Store or TestFlight operations
- any recruiter-facing claim of a finished product

## Why the UI ones are grouped

The first five are one decision, not five. The engine is being built first specifically so
that the interface can be designed against real latency, real evidence density, real
confidence distributions, and real failure modes — rather than against imagined versions of
them, which is how attractive screens get built around backend behaviour that does not exist.

A stable command-and-snapshot boundary already exists for the future client
(`CashBooksLocalEngineHost`). What is deferred is the experience, not the seam.

The generated SwiftUI shell may remain in place so the iOS and macOS targets keep building.
It does not participate in G3, and no feature view may be added to it.

## Why the others are blocked

**Plaid, backend or network sync.** The milestone's privacy guarantee is that document bytes
and extracted text never cross a network boundary. Adding a network dependency now would put
that guarantee in tension with a feature before the guarantee has protected tests.

**Share Extension, standalone image or camera ingestion.** Each is an additional trust
boundary and an additional acquisition lifecycle. The in-app Files importer is the one path
that has to work first.

**Broader accounting workflows.** The product is a trust layer over document ingestion, not a
QuickBooks clone. Scope creep here is the most likely way this project becomes unfinishable.

**App Store or TestFlight operations.** Distribution follows a product, and there is no
product yet.

**Any recruiter-facing claim of a finished product.** G3 produces a verified headless engine.
Describing it as a shipped app is not an exaggeration to be walked back later — it is a claim
a competent interviewer will test, and failing that test costs more than the claim ever
bought. See the claims discipline in `docs/program/ROADMAP.md`.

## Related

- `docs/product/ACTIVE_SCOPE.md` — what *is* authorized
- `docs/program/ROADMAP.md` — the deferred programs and what each would need
