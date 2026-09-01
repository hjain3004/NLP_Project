> **Authority: Historical (2026-09-01).** A serial task inventory. The active program is a dependency DAG with gates, not a list.
> The active authority order is defined in [docs/DOCUMENT_AUTHORITY.md](docs/DOCUMENT_AUTHORITY.md).
> Nothing in this document authorizes work. Treat it as input, not instruction.

# TASKS.md — CashGuard Gated Task Breakdown

Execute in order. Each task lists: spec references → deliverables → GATE (commands that must pass + criteria). Do not start a task until the previous task's gate is green or the task is formally BLOCKED per CLAUDE.md. Tasks marked ∥ may be done in either order within their phase.

## Phase 0 — Setup
**T0.1 Repo + toolchain.** Refs: TECH_SPEC §2,§3,§14; PRD §17. Deliver: repo layout, SwiftPM `CashGuardKit`, multiplatform `CashGuardApp` (iOS 26 + macOS), backend skeleton, `.env.example`, Makefile with all §14 targets (stubs OK), `docs/` seeded with empty PROGRESS/DECISIONS/BLOCKERS/REVIEW_QUEUE/PATTERNS/METRICS.
GATE: `make verify` green (empty suites count); `make app-build` succeeds; README skeleton per PRD §19 opening.

## Phase 1 — Domain + persistence
**T1.1 Domain structs + @Model records.** Refs: TECH_SPEC §1,§4; PRD §6. Deliver: all entities, DTO↔record mapping, provenance enum on Evidence.
GATE: `make ios-test` — serialization round-trip tests for every persisted entity; schema loads in a fresh container.
**T1.2 Audit log + redaction.** Refs: TECH_SPEC §12; PRD §12.3(log rules §10.7). Deliver: AuditEntryRecord writer, `redact()` helpers wired into logging.
GATE: redaction unit tests (digit-runs, untrusted tags) pass; grep-test proves no `print(` in Kit.
**T1.3 Fixture loader + demo entities.** Refs: TECH_SPEC §15; PRD §14. Deliver: `Fixtures/` loader, initial demo invoices/vendors JSON.
GATE: fixture load test creates full demo business locally (no Plaid yet).

## Phase 2 — Plaid integration
**T2.1 Backend link/exchange/sync.** Refs: TECH_SPEC §7,§8; PRD §11. Deliver: routes, SQLAlchemy models, cursor-persistent sync service, amount-sign normalization with test.
GATE: `make backend-test` — respx tests for pagination (has_more loop), modified/removed handling, sign flip, webhook ack.
**T2.2 Sandbox seeding.** Refs: TECH_SPEC §15. Deliver: `demo_business.json`, `seed.py` (creates user_custom + user_transactions_dynamic items via /sandbox/public_token/create), count assertion + retry, `/api/demo/inject`, `/api/demo/reset`.
GATE: `PLAID_LIVE_TEST=1 make seed` against real sandbox succeeds twice consecutively; inject endpoint returns a transaction visible on next sync.
**T2.3 iOS Link + sync client.** Refs: TECH_SPEC §7,§8. Deliver: LinkKit flow, BackendClient with retry policy, SyncState, staleness banners, manual Sync Now.
GATE: `make ios-test` — client tests against a stub URLProtocol (retry, error mapping, tombstoning); `make app-build`; manual checkpoint logged in PROGRESS: Link→transactions list renders in Simulator.

## Phase 3 — Cash engines (all pure, in Kit)
**T3.1 Canonicalization + vendor profiles.** Refs: TECH_SPEC §6.1; PRD §8.3,§8.8. GATE: 20 merchant vectors pass; profile stats (median, range, frequency, lastPaid) match hand-computed fixture.
**T3.2 ∥ Recurring detection.** Refs: TECH_SPEC §5,§6; PRD §8.5. GATE: detects payroll/rent/subscription from demo fixture; rejects irregular series; period bucket edge tests (13d vs 15d).
**T3.3 ∥ Invoice matching + status.** Refs: TECH_SPEC §6.2; PRD §8.4. GATE: tier tests (high/medium/low), no-auto-confirm invariant test.
**T3.4 Safe-to-spend + runway.** Refs: TECH_SPEC §6.3,§6.4; PRD §8.6,§8.7. GATE: fixture math matches hand calc; property tests (PRD §15.6) 200 iterations green; raw-vs-clamped semantics tested.
**T3.5 Cash Action Generator v1 (cash actions only).** Refs: PRD §8.14,§3.3. GATE: demo fixture produces expected chase/preserve/safe-to-spend actions in priority order (golden test).

## Phase 4 — Risk engines
**T4.1 Fingerprint manager + TOFU.** Refs: TECH_SPEC §6.5; PRD §8.9. GATE: state-machine tests cover every legal transition + rejection of illegal ones (e.g., repin without verification); hash stability vectors.
**T4.2 Payment request risk engine.** Refs: TECH_SPEC §6.6,§9; PRD §8.10. Deliver: signal table as data, weak-signal capped bucket, deterministic scanners on raw text.
GATE: per-signal unit tests; threshold boundary tests (19/20, 39/40, 69/70); weak-signals-cannot-block property test.
**T4.3 ∥ Outflow anomaly engine.** Refs: PRD §8.11. GATE: new-merchant-large, spike, dormant, velocity, duplicate signals each unit-tested pos+neg; runs automatically in sync pipeline test.
**T4.4 ∥ Duplicate engine.** Refs: TECH_SPEC §5; PRD §8.12. GATE: invoice-number match > amount+window > amount-only confidence ordering tests.
**T4.5 Verification workflow + ledger.** Refs: PRD §8.13,§13.4; TECH_SPEC §9.5. GATE: checklist is static resource (test asserts no model/document string reaches it); ledger entry required to repin; contact binding test.
**T4.6 Labeled corpus v1 + runner.** Refs: TECH_SPEC §13.4; PRD §15.7-15.9. Deliver: 50 scenarios (35/15 per PRD variants incl. Urgent Care FP + masked-change), `make corpus`, METRICS.md generation.
GATE: `make corpus` exits 0 (recall ≥0.80 suspicious, FP budget met). If not met: tune ONLY via EngineConfig constants, record each tuning iteration in PROGRESS, re-run. Threshold table changes require DECISIONS entry.

## Phase 5 — Firewall + agent layer
**T5.1 PromptFirewall + UntrustedTextDocument pipeline.** Refs: PRD §5.5,§10.2-10.3; TECH_SPEC §9. GATE: the 5 enforcement-point tests in TECH_SPEC §9 (tool-less assertion, provenance rejection, max() merge, lint test, checklist binding).
**T5.2 RuleBasedAgent (full).** Refs: TECH_SPEC §10. GATE: regex extractor tests on 10 pasted-request fixtures; template outputs decode + pass post-generation validation; `extractMemory` returns [].
**T5.3 FoundationModelsAgent.** Refs: TECH_SPEC §2.5,§10,§11; PRD §9. Deliver: availability routing, sessions per capability, @Generable types, post-generation validators, `generatedBy` stamping.
GATE: `make ios-test` still green WITHOUT FoundationModels (Kit unaffected); `make app-build` green; compile-time proof AgentFM only in app target. Manual checkpoint: run 3 golden missions in Simulator, transcripts saved.
**T5.4 Adversarial suite.** Refs: TECH_SPEC §13.6; PRD §14.7,§15.5. GATE: all adversarial fixtures pass against RuleBasedAgent+validators in `make ios-test`.
**T5.5 Policy patches + memory extraction.** Refs: PRD §7; TECH_SPEC §10. GATE: provenance rule test (documentText evidence → reject); auto-learn enum whitelist test; approval-required list matches PRD §7.4 exactly.

## Phase 6 — UX + demo
**T6.1 Inbox + evidence detail + risk review + anomaly alert screens.** Refs: PRD §13.1-13.3,§13.6. GATE: `make app-build`; snapshot-or-manual checklist in PROGRESS (every action shows evidence, freshness, untrusted-quoting).
**T6.2 Verification, policy, memory, vendor/fingerprint screens.** Refs: PRD §13.4-13.5. GATE: Face ID gate on approvals (DEBUG fallback per TECH_SPEC §12); policy patch old→new diff UI.
**T6.3 Demo runbook + reset.** Refs: TECH_SPEC §15; PRD §14. Deliver: `docs/demo-runbook.md` scripting all 7 scenarios end-to-end (which are seeded vs injected vs pasted).
GATE: execute the full runbook in Simulator twice consecutively; log timings in PROGRESS.

## Phase 7 — Ingestion upgrade (pick BOTH if time, else scan first)
**T7.1 DocumentScan/OCR source.** Refs: PRD §5.3. GATE: image fixture → OCR text → tool-less extraction → validated draft → confirm flow test (Vision path mocked in Kit tests; live path manual checkpoint).
**T7.2 StripeTestSource.** Refs: PRD §5.4; TECH_SPEC §7. GATE: backend respx test mapping Stripe invoice JSON → InvoiceDraft; adapter conforms to InvoiceSource; feature-flagged off when key absent.

## Phase 8 — Hardening + docs
**T8.1 Full regression + metrics refresh.** GATE: `make verify` + `make corpus` green; METRICS.md current; zero adversarial regressions.
**T8.2 README + THREAT_MODEL + architecture diagram.** Refs: PRD §19,§10.4,§20. GATE: README contains metrics table, authority matrix summary, honest-claims language ONLY (grep-test forbidden phrases: "fraud firewall", "prevents fraud", "verifies vendor bank account").
**T8.3 Review queue closure.** GATE: REVIEW_QUEUE has zero `must-review`; `should-review` items summarized at top for the human; final STATUS entry in PROGRESS (what shipped, metrics, known limitations mapped to PRD §20).
