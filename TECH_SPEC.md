> **Authority: Reference (2026-09-01).** July 2026 technical decisions, re-adopted only by explicit incorporation into a current document.
> The active authority order is defined in [docs/DOCUMENT_AUTHORITY.md](docs/DOCUMENT_AUTHORITY.md).
> Nothing in this document authorizes work. Treat it as input, not instruction.

# TECH_SPEC.md — CashGuard Technical Specification (v1.0)

Companion to `cashguard_prd_v0_2.md`. Every decision here is FINAL unless it contradicts the PRD's Authority Matrix or security sections. The implementing agent follows this without re-deliberating. PRD section references are given as (PRD §x).

## 1. Global conventions
- **Money**: `Int` cents, signed. Outflows negative, inflows positive, everywhere in the app after normalization. A single `Money` typealias with formatting helpers. Currency: USD only for MVP; hardcode `"USD"` in one constant.
- **Dates**: transaction/invoice dates are calendar dates (no time). Store as `Date` truncated to start-of-day in the user's current `Calendar`/timezone captured at computation time. All "days between" math uses `Calendar.dateComponents([.day])`.
- **IDs**: `UUID` for all local entities. Plaid transaction IDs kept as `String` in a dedicated field, unique-indexed.
- **Naming**: engines are pure and stateless — `struct XEngine { func run(input) -> Output }` — with all tunables passed in via `EngineConfig` (see §5) or the `BusinessPolicyProfile`. No engine reads global state or the clock directly; `now: Date` is always a parameter (testability).
- **Errors**: typed `enum CashGuardError: Error` domains per layer (`.network`, `.plaid`, `.persistence`, `.agent`, `.validation`). No `fatalError` outside programmer-assertion paths unreachable in production.

## 2. Stack decisions (final)
- **2.1 Persistence**: SwiftData. `@Model` classes for persisted entities (§4). Engine inputs/outputs are plain `Codable` structs (DTOs); persisted results (RiskDecision, alerts, audit, verification ledger, drafts) are mapped into `@Model` records by a thin store layer. No SQLite hand-rolling.
- **2.2 iOS min target**: iOS 26.0. Multiplatform target (iOS + macOS) enabled from Phase 0; macOS is the guaranteed live-model demo path.
- **2.3 Backend**: Python 3.12, FastAPI, SQLAlchemy 2 + SQLite, `plaid-python`, `uvicorn`, `pydantic-settings`, `httpx` (for Stripe test source, Phase 7). Tests: `pytest`, `respx`.
- **2.4 Dependency allowlist** (adding anything else requires a DECISIONS entry + REVIEW_QUEUE `should-review`): iOS → `plaid-link-ios` (LinkKit, SPM) ONLY. Backend → the list in 2.3 plus `python-dotenv`, `ruff` (lint). Nothing else. No networking, DB, or AI wrapper libraries.
- **2.5 Foundation Models**: use `SystemLanguageModel.default`, `LanguageModelSession`, `Tool`, `@Generable`/`@Guide`, `streamResponse`/`respond(to:generating:)`. VERIFY exact signatures against the installed SDK before first use (API shifted across betas); record any deviation from PRD pseudo-code in DECISIONS.

## 3. Module layout (iOS)
```
CashGuardKit/            # SPM package, platform-independent Swift
  Domain/                # entities as structs + @Model mirrors
  Engines/               # all §8 PRD engines, pure functions
  Firewall/              # PromptFirewall, UntrustedTextDocument handling
  Agent/                 # PaymentAgent protocol + RuleBasedAgent (FM-free)
  Fixtures/              # synthetic corpus, demo scenarios, test vectors
CashGuardApp/            # SwiftUI app target (iOS + macOS)
  AgentFM/               # FoundationModelsAgent (imports FoundationModels)
  Views/  Services/  Stores/
backend/                 # FastAPI per PRD §16
```
Rationale (final): `CashGuardKit` compiles and tests WITHOUT FoundationModels — this is what makes the full suite runnable headless. Only `AgentFM` imports the framework.

## 4. Persisted entities (SwiftData `@Model`)
Persist exactly: `BusinessProfileRecord`, `PolicyProfileRecord` (JSON-encoded policy blob + version), `CustomerIntelligenceRecord`, `VendorRecord` (incl. pinned fingerprints as owned collection `FingerprintRecord`), `InvoiceRecord`, `PaymentRequestRecord`, `NormalizedTransactionRecord`, `UntrustedTextDocumentRecord`, `RiskDecisionRecord`, `OutflowAnomalyAlertRecord`, `CashActionRecord`, `PolicyPatchRecord`, `VerificationLedgerRecord`, `AuditEntryRecord`, `SyncStateRecord`. Everything else is computed on demand. Relationships: Vendor 1→N Fingerprint, Vendor 1→N PaymentRequest; Invoice 1→N reminder drafts (embedded JSON is fine). Deletion: soft-delete via `archivedAt` on user-facing records; hard delete only via demo reset.

## 5. Engine constants (single source: `EngineConfig.default`, overridable by policy where PRD says configurable)
| Constant | Value | Used by |
|---|---|---|
| `recurringMinOccurrences` | 3 | RecurringOutflow |
| `recurringAmountTolerance` | ±15% of median | RecurringOutflow |
| `recurringPeriodBuckets` | weekly 7±2, biweekly 14±3, monthly 30±4 (days) | RecurringOutflow |
| `inflowConfidenceWeights` | dueFuture 0.9, overdue≤15d 0.6, overdue>15d 0.3, disputed 0.0 | SafeToSpend |
| `safeToSpendHorizonDays` | 14 | SafeToSpend |
| `runwayProjectionDays` | 60 | CashRunway |
| `runwayBreachDefinition` | first projected day balance < policy reserve | CashRunway |
| `dormancyDays` | max(3 × vendor typicalFrequencyDays, 90) | Risk engines |
| `velocityWindowDays` / `velocityMinRequests` | 7 / 3 | Risk engines |
| `duplicateWindowDays` | 7 | Duplicate |
| `duplicateAmountTolerance` | max(100¢, 1% of amount) | Duplicate |
| `amountSpikeMultiplier` | 2.0 → +30; ≥4.0 → +45 (linear in between, rounded) | Risk engines |
| `newVendorLargeOutflowCents` | 50_000 default (policy-overridable) | OutflowAnomaly |
| `staleWarnHours` / `stalePauseHours` | 24 / 48 | Stale data (PRD §10.6) |
| `urgencyKeywords` | ["urgent","immediately","asap","final notice","act now","wire today","past due - pay now"] | Risk engines |

Urgency rule detail (final): case-insensitive whole-phrase match on raw memo text, SKIPPED when the matched phrase is a substring of the vendor's canonical name or aliases (kills "Urgent Care Clinic LLC" FP).

## 6. Key algorithms (final)
- **6.1 Merchant canonicalization** (`canonicalMerchant`): uppercase → strip processor prefixes (`SQ *`, `TST*`, `PAYPAL *`, `PY *`, `SP `) → remove punctuation → collapse whitespace → drop trailing all-digit tokens and `#1234` store codes → strip legal suffixes (`LLC`,`INC`,`CO`,`CORP`,`LTD`,`PLLC`). Ship with the 20 test vectors in `Fixtures/merchant_vectors.json` (write them; include GUSTO PAY 123456→GUSTO PAY? No: also strip trailing digit-groups ≥4 → `GUSTO PAY`).
- **6.2 Invoice matching tiers**: exact plaidTxn.amount == invoice.amount AND txn.date ≥ invoice.issuedAt → `high` (auto-suggest, 1-tap confirm). Amount within duplicateAmountTolerance AND date ≥ issuedAt → `medium`. Amount-only match → `low` (shown under "possible"). Never auto-confirm (PRD §4.2).
- **6.3 Safe-to-spend**: `raw = availableBalance − policy.reserveCents − Σ obligationsDue(horizon) + Σ (openInvoiceAmount × confidenceWeight)` where obligations = recurring detections + user-entered scheduled outflows. Expose `raw`, `displayValue = max(raw, 0)`, and `shortfallCents = max(−raw, 0)`. Property tests per PRD §15.6 target `raw` (monotonic), not the clamp.
- **6.4 Runway**: day-by-day 60-day projection: start availableBalance; apply recurring outflows on `nextExpectedDate` cadence, weighted invoice inflows on `dueAt` (weight as §5), user scheduled items. Output timeline + `firstReserveBreachDay?` + `firstNegativeDay?`.
- **6.5 Fingerprint** (PRD §8.9): `SHA256(lowercased "\(method)|\(accountLast4 ?? "-")|\(routingLast4 ?? "-")|\(payeeHandle ?? "-")")`, hex-encoded; store components alongside hash for diff display. State machine transitions exactly as PRD enum; ONLY VerificationWorkflowEngine may emit `repinnedAfterVerification`.
- **6.6 Risk scoring**: implement the PRD §8.10 / §8.11 tables verbatim as data (a `[RiskSignal: Weight]` table in code), not as scattered ifs — the corpus runner and README metrics read from the same table. Decision thresholds verbatim (0-19/20-39/40-69/70+). Enforce the weak-signal property structurally: weak signals (urgency, round-amount, vague-memo) are summed into a capped bucket (max +8 total).

## 7. Backend API contract (all JSON; bearer token `X-CG-Token: <dev token from .env>`)
| Route | Request | Response 200 |
|---|---|---|
| `POST /api/link/token` | `{}` | `{"link_token": str, "expiration": iso8601}` |
| `POST /api/link/exchange` | `{"public_token": str}` | `{"item_id": str, "accounts": [Account]}` |
| `GET /api/accounts` | — | `{"accounts": [Account], "as_of": iso8601}` |
| `GET /api/transactions?cursor=<opaque>` | — | `{"added":[Txn],"modified":[Txn],"removed":[str],"next_cursor":str,"synced_at":iso8601}` |
| `POST /api/sync` | `{}` | runs Plaid sync now → same shape as GET /api/transactions |
| `POST /api/webhook/plaid` | Plaid webhook body | `{"ok":true}` (always 200; verify type==TRANSACTIONS) |
| `POST /api/demo/inject` | `{"amount_cents":int,"description":str,"date":iso-date?}` | `{"transaction_id":str}` |
| `POST /api/demo/reset` | `{"scenario":"default"}` | `{"item_ids":[str]}` |
| `GET /api/stripe/invoices` (Phase 7) | — | `{"invoice_drafts":[InvoiceDraft]}` |
`Txn` = PRD canonical schema §11 with `amount_cents: Int` signed (backend flips Plaid's positive-debit convention; document in `normalize.py` with a test). `Account` = `{id, name, mask, type, subtype, available_cents, current_cents}`. Errors: `{"error": {"code": str, "message": str}}` with 4xx/5xx; iOS maps to `CashGuardError.network(code)`.
Retry policy (iOS): GETs retry 3× exponential backoff (0.5/1/2s); POSTs never auto-retry except `/api/sync` (idempotent by design).

## 8. Sync + staleness (final)
`SyncStateRecord` holds `lastSyncedAt`, `cursor`. Foreground refresh: if `now − lastSyncedAt > 15 min`, call `/api/sync`. Removed transaction IDs tombstone local records and re-run affected engines. Staleness banners per §5 constants; when paused (>48h) the Cash Action Inbox shows the pause card (PRD §10.6) and risk engines still run but actions carry `dataFreshness: stale`.

## 9. Monotone influence — enforcement points (final; PRD §9.8/§10.3)
1. `PaymentRequestDraft` model-extracted booleans (`mentionsPaymentDetailChange`, urgency-ish flags) may map ONLY to additive signals; the risk engine ALSO runs deterministic scanners (fingerprint compare from stored data; keyword scan on raw text) and takes `max(deterministic, model-suggested)` per signal — model output can raise a signal deterministically found absent, never lower one found present.
2. Extraction sessions: constructed with `tools: []`. Assert in a unit test that `FoundationModelsAgent.makeExtractionSession().tools.isEmpty` (and equivalent for RuleBasedAgent path).
3. `PolicyPatch` validation rejects any patch whose `evidenceIDs` include evidence with `provenance == .documentText` (enum: `.userAction`, `.engineComputed`, `.documentText`).
4. Explanation renderer: any string sourced from `UntrustedTextDocument` renders through `UntrustedTextView` (quoted, labeled); direct interpolation of untrusted text into explanation bodies is forbidden — provide a lint test greping for direct `.rawText` use outside Firewall/UI-quarantine files.
5. Verification checklist content is a static resource; phone/email fields bind exclusively to `VendorRecord` stored contact.

## 10. PaymentAgent protocol + fallback matrix (final)
```swift
protocol PaymentAgent {
  func parsePaymentRequest(_ doc: UntrustedTextDocument) async throws -> PaymentRequestDraft
  func parseInvoice(_ doc: UntrustedTextDocument) async throws -> InvoiceDraft
  func explain(_ ctx: ActionExplanationContext) async throws -> [CashActionExplanation]
  func draftReminder(_ ctx: InvoiceReminderContext) async throws -> InvoiceReminderDraft
  func proposePatches(_ ctx: PolicyPatchContext) async throws -> [PolicyPatchDraft]
  func extractMemory(_ ctx: InteractionSummaryContext) async throws -> [ProfileUpdateCandidate]
}
```
| Capability | FoundationModelsAgent | RuleBasedAgent (always available) |
|---|---|---|
| parsePaymentRequest / parseInvoice | tool-less guided generation → draft | regex extractors: currency amounts, dates, acct/routing digit runs, vendor line heuristic → prefilled form the user completes |
| explain | generation grounded in evidence summaries; evidence-ID validation post-pass | template strings per action kind filled from evidence fields |
| draftReminder | generated, tone from CustomerIntelligence | 3 static templates (polite/firm/final) with merge fields |
| proposePatches | generated from deterministic trigger contexts only | deterministic triggers (e.g., 3 rejections above reserve) → templated patch |
| extractMemory | typed candidates from interaction log | disabled (returns []) |
Availability routing: `AgentProvider.resolve()` → FM if `SystemLanguageModel.default.availability == .available` else RuleBased; current mode always visible in Settings and stamped on every generated artifact (`generatedBy` field).
Post-generation validation (both agents): evidence-ID whitelist check, length caps, no digits-only "new contact" strings in reminder/explanation bodies (regex), schema decode. Failures → discard output, fall back to RuleBasedAgent for that call, log `agentValidationFailure` audit event.

## 11. Prompt templates (FoundationModelsAgent; keep ≤ ~1.5k tokens total per session)
- Session `instructions` (static, per capability) live in `AgentFM/Prompts/*.swift` as constants; contents per PRD §9.6 plus the untrusted-data clause from PRD §5.5. NEVER interpolate document text into instructions — untrusted text goes only in the user-turn prompt of tool-less extraction sessions.
- Explanation sessions receive: action list (IDs+kinds), evidence summaries (typed, pre-rendered ≤60 words each), policy summary line, style profile line. Never raw transactions.

## 12. Security implementation notes
- `.env` never committed; `Config.load()` fails fast with a human-readable message listing missing keys (this is a legitimate STOP condition).
- Log redaction: a `redact()` helper masks digit runs ≥4 and anything tagged untrusted; wrap all backend logging + iOS `Logger` category `cashguard` through it. Test: seeded run produces zero unredacted digit-runs in captured logs.
- Face ID (LocalAuthentication) gate on: approving `verifyBeforeApproval`/`blockUntilVerified` requests and applying policy patches. Simulator/dev fallback: device passcode / always-allow behind `DEBUG` flag recorded in DECISIONS.

## 13. Testing architecture (final)
- 13.1 `CashGuardKit` tests: pure XCTest, no FoundationModels import, runs via `xcodebuild test -scheme CashGuardKit -destination 'platform=macOS'` (fast, headless).
- 13.2 Backend: `pytest` with `respx` intercepting Plaid HTTP; one live-sandbox integration test behind `PLAID_LIVE_TEST=1`.
- 13.3 Property tests: implement PRD §15.6 list with 200-iteration randomized harness (seeded RNG, seed printed on failure).
- 13.4 Corpus runner (`make corpus`): loads `Fixtures/corpus/v1/*.json` (50 labeled scenarios per PRD §15.7), runs both risk engines, emits `docs/METRICS.md` (recall, precision, FP-rate, confusion matrix, per-signal firing counts) and exits nonzero if recall < 0.80 on suspicious or FP-budget violated in the normal-week simulation fixture.
- 13.5 Live-model eval (MANUAL scheme `CashGuardEval`, macOS target): runs the 20-mission golden set + 10 adversarial extractions against FoundationModelsAgent, writes transcripts to `docs/evals/transcripts/`; scoring is the human rubric — the scheme only automates transcript capture and schema/evidence validation.
- 13.6 Adversarial fixtures (PRD §15.5 + §14.7): injection-in-memo, masked detail change, fake verification phone, instruction-in-invoice. Expected: risk unchanged-or-raised, untrusted rendering, no contact leakage. These run in 13.1 against RuleBasedAgent + validators, and in 13.5 against the live model.

## 14. Make targets (create in Phase 0; these are the gates' vocabulary)
`make backend-run`, `make backend-test`, `make ios-test` (=13.1), `make app-build` (iOS Simulator build), `make corpus`, `make verify` (= backend-test + ios-test + app-build), `make seed` (backend sandbox seeding), `make demo-reset`.

## 15. Demo seeding (final)
`backend/sandbox/demo_business.json` implements PRD §14 scenarios on the `user_custom` item (Meridian-style history: payroll biweekly, rent monthly on 1st, subscriptions, client deposits, PrintWorks ~$400 monthly, one dormant annual vendor for scenario 5); scenario events that must be "new" during the demo (BEC request, anomaly outflow, duplicate) are injected live via `/api/demo/inject` on the `user_transactions_dynamic` item or entered via paste flow. `seed.py` asserts post-seed transaction counts and retries once (known sandbox custom-user flakiness). All 7 PRD §14 scenarios get a scripted step list in `docs/demo-runbook.md` (Phase 6 task).
