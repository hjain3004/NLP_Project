# CashBooks Release-Blocking Invariants

**Status:** Active. This file is the single authoritative home of the invariants.
**Written:** P0 Task 5, 2026-09-01.

Twenty-six properties. Each is release-blocking: a build that violates one does not ship,
regardless of what else works.

`AGENTS.md` repeats the invariant statements for workers; this file governs.
Protected tests are assigned as each invariant is implemented through the milestones in
`docs/ENGINE_BUILD_PLAN.md`. An invariant with no protected test remains an intention.

## Financial

| ID | Statement | Why | Protected test | Blocks |
|---|---|---|---|---|
| **FIN-01** | Money uses fixed-point integer minor units, never `Double` | Binary floating point cannot represent most decimal money values exactly. Accumulated rounding in a ledger is not a cosmetic defect — it is a wrong number in someone's books. | unassigned | G2, G3-B |
| **FIN-02** | Currency and rounding policies are explicit | An implicit rounding mode is a decision nobody made and nobody can review. Explicit policy makes the behaviour testable and the choice auditable. | unassigned | G2, G3-B |
| **FIN-03** | Imports, retries, and confirmations are idempotent | Every one of these can be repeated by a crash, a retry, or an impatient tap. Non-idempotent financial writes turn transient faults into duplicate records. | unassigned | G3-B |
| **FIN-04** | Duplicate documents cannot silently create duplicate records | The same invoice arrives twice — forwarded, re-scanned, re-imported. Silently booking it twice is the single most likely way this product damages a user's books. | unassigned | G3-B |
| **FIN-05** | Persistence of the confirmation aggregate is atomic | The record, its evidence links, the risk decision, the audit event, and the fingerprint transition are one fact. A partial write is a record whose provenance cannot be reconstructed. | unassigned | G3-B |
| **FIN-06** | Locale-sensitive parsing cannot alter stored monetary meaning | `1.234` is one thing in one locale and a thousand times that in another. Parsing that depends on ambient device settings makes stored values a function of where the phone was configured. | unassigned | G2, G3-B |

## AI authority

| ID | Statement | Why | Protected test | Blocks |
|---|---|---|---|---|
| **AI-01** | Foundation Models output is an untrusted draft | The model is an extraction convenience, not an authority. Treating its output as trusted collapses every other guarantee in this table. | unassigned | G2, G3-B |
| **AI-02** | Every material field retains source evidence | A number the user cannot trace back to a page and a span is a number they have to take on faith. Evidence is what makes correction possible and review meaningful. | unassigned | G2, G3-B |
| **AI-03** | Deterministic validation precedes persistence | Validation after the write is a report, not a control. | unassigned | G3-B |
| **AI-04** | Model influence may add but not reduce deterministic risk | Monotonicity is the entire defence against prompt injection. A document that argues its own payment change is legitimate must not be able to act on that argument. | unassigned | G2, G3-B |
| **AI-05** | Rule-based fallback remains functional | Most devices cannot run the model. A product that degrades to unusable without Apple Intelligence is a product for a minority of its own users. | unassigned | G3-B |
| **AI-06** | Model-generated IDs are not authoritative storage IDs | A generated identifier is a plausible string, not a guarantee of uniqueness or stability. Using one as a primary key imports the model's failure modes into the database. | unassigned | G2, G3-B |
| **AI-07** | No extractor, model adapter, risk engine, coordinator, or test harness can mint confirmation authorization | Confirmation is the moment a user takes responsibility. Any component that could manufacture it could write to the ledger on their behalf without their knowledge. | unassigned | G2, G3-B |

## Document privacy

| ID | Statement | Why | Protected test | Blocks |
|---|---|---|---|---|
| **PRIV-01** | PDF ingestion performs no network requests | The local-only claim is testable exactly once: as an enforced boundary with a runtime sentinel. As a promise, it is worth nothing. | unassigned | G3-B |
| **PRIV-02** | Document bytes and extracted text remain local | Invoices contain bank details, addresses, and trading relationships. This is the guarantee the product is sold on. | unassigned | G3-B |
| **PRIV-03** | Security-scoped access is released correctly | A leaked scope is a permission the user granted for one operation and unknowingly kept granting. | unassigned | G3-B |
| **PRIV-04** | Temporary data has explicit cleanup | Staged copies of financial documents accumulating in a cache directory is a data-retention problem the user never agreed to. | unassigned | G3-B |
| **PRIV-05** | Logs and telemetry contain no document text or PII | Diagnostics are the most common accidental exfiltration path, because nobody thinks of a log as a data flow. | unassigned | G3-B |
| **PRIV-06** | Malformed, encrypted, oversized, and unsupported documents fail safely | Untrusted binary input from strangers is the attack surface. Failing safely is the difference between a rejected file and an exploited parser. | unassigned | G3-B |

## Platform correctness

| ID | Statement | Why | Protected test | Blocks |
|---|---|---|---|---|
| **PLAT-01** | Core compiles without Foundation Models, PDFKit, Vision, SwiftUI, SwiftData, or networking | The boundary is what keeps the domain logic testable in milliseconds, on any machine, without a simulator or a model. A forbidden-import check proves it; a convention does not. | unassigned | G1, G2 |
| **PLAT-02** | Framework objects remain actor-owned | `PDFDocument` and its kin are not `Sendable`. Sharing them across actors is a data race that appears as intermittent corruption rather than a crash. | unassigned | G3-B |
| **PLAT-03** | Engine commands and snapshots crossing actors are immutable and `Sendable` | The boundary the future client consumes must be safe by construction, not by discipline. | unassigned | G2, G3-B |
| **PLAT-04** | `ModelContext` and model records do not cross actors | SwiftData model instances are bound to their context. Passing them out of the repository actor is the most common way a SwiftData app fails in production and not in testing. | unassigned | G3-B |
| **PLAT-05** | Foundation Models availability is detected | Availability varies by device, region, language, and settings. Assuming it is a crash on most hardware. | unassigned | G3-B |
| **PLAT-06** | Automated tests do not require a live model | A suite that needs Apple Intelligence runs on one machine and in no CI. Determinism here is what makes the other twenty-five checkable. | unassigned | G3-B |
| **PLAT-07** | Failures and cancellation leave no partial canonical record | The user tapped cancel, or the app was killed. Either way the ledger must look exactly as it did before they started. | unassigned | G3-B |

## Changing one of these

Any change requires the owner's explicit decision in a dedicated pull request with
rationale and independent review. Protected tests belonging to an invariant may not be
weakened, renamed, skipped, or removed in a feature pull request.

## Related

- `AGENTS.md` — the worker-facing statement
- `docs/security/PRIVACY_BOUNDARY.md` — the two privacy boundaries and why they differ
