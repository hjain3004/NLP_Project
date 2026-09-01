# CashBooks Control Plane and Recruiter-First Vertical Slice

> **Authority notice (2026-08-31):** The product, privacy, document-ingestion,
> deterministic-authority, and recruiter-first decisions in this document remain
> valid. Its single-agent orchestration model is superseded by
> [CashBooks Multi-Model Engineering System](./2026-08-31-cashbooks-multi-model-agent-system-design.md).
> When the documents conflict about planning, concurrency, task state, review,
> handoffs, model routing, or integration, the multi-model design wins.

**Status:** Approved in conversation; awaiting written-spec review  
**Date:** 2026-08-30  
**Product name:** CashBooks  
**Primary platform:** iOS  
**Supporting platform:** macOS model-validation harness  

## 1. Decision summary

CashBooks will use a compact project control plane that lets lower-cost coding agents execute narrowly scoped work without re-deciding product strategy, architecture, security boundaries, or quality standards.

The project will not attempt to implement the full existing PRD first. Its first milestone is a recruiter-focused vertical slice proving the project's hardest and most differentiated ideas:

- local PDF ingestion;
- per-page text-layer or OCR extraction;
- field-level evidence lineage;
- typed on-device model extraction;
- deterministic validation and risk authority;
- payment-instruction fingerprint comparison;
- trusted-contact verification;
- safety-preserving fallback; and
- adversarial, correctness, and performance evaluation.

Synthetic financial fixtures will be used first. Plaid Sandbox integration and the Mail/Files Share Extension are deferred until the vertical slice is stable.

## 2. Problem

The repository contains unusually detailed product and technical research, but it does not yet provide a compact, consistent execution contract for coding agents. Current problems include:

- the product is named AppleBooks in the Xcode scaffold and CashGuard in the specifications;
- the new canonical name is CashBooks;
- document paths and PRD filenames disagree across existing files;
- the full PRD is much broader than the highest-value recruiter demonstration;
- the PDF ingestion design does not distinguish text PDFs from scanned PDFs;
- the existing task plan can encourage broad phase execution before the core differentiator is proven; and
- a lower-cost agent could plausibly reinterpret architecture or weaken a security invariant while resolving ambiguity.

The control plane must make the safe path easier than improvisation.

## 3. Goals

### 3.1 Project-control goals

- Give every coding agent one canonical operating contract.
- Separate permanent safety invariants from milestone-specific requirements.
- Break work into bounded tasks that fit comfortably in one model context.
- Define exact inputs, outputs, allowed files, tests, and verification gates for every task.
- Preserve actual command output, decisions, metrics, and blockers across agent sessions.
- Prevent agents from silently expanding scope, adding dependencies, or changing authority boundaries.
- Make the repository understandable to a recruiter within 60 seconds.

### 3.2 First-milestone goals

- Import a PDF from an in-app Files picker.
- Process the document without sending its bytes or extracted text over the network.
- Extract embedded text with PDFKit when it is usable.
- Render and OCR only pages whose text layer is missing or fails an explicit quality check.
- Preserve source page and extraction method for every material draft field.
- Produce a typed invoice or payment-request draft through Foundation Models when available.
- Produce a safe, usable draft through a deterministic fallback when Foundation Models is unavailable or rejected.
- Compare payment instructions with a previously confirmed vendor fingerprint.
- Prevent document-provided contact details from becoming verification instructions.
- Require the owner to review and confirm all material fields before canonical persistence.
- Demonstrate through tests that model output cannot lower deterministic risk.

### 3.3 Recruiting goals

- Demonstrate the boundary between probabilistic inference and deterministic software.
- Demonstrate native Apple-platform depth rather than a hosted-model wrapper.
- Demonstrate financial correctness, provenance, privacy, accessibility, and failure handling.
- Produce measurable evaluation and performance evidence instead of feature claims.
- Support a reproducible 60-90 second hero demo.

## 4. Non-goals for the first milestone

- Replacing QuickBooks or FreshBooks.
- Double-entry accounting, reconciliation, tax, payroll, or money movement.
- Plaid integration or a production backend.
- Mail/Files Share Extension.
- Standalone image import or camera scanning.
- QuickBooks, FreshBooks, Gmail, Stripe, or other connectors.
- Cloud LLMs or Private Cloud Compute.
- Centralized fraud intelligence or cross-customer learning.
- Automatic approval of an invoice or payment request.
- Production claims about fraud detection efficacy.
- Full macOS product parity.

Scanned PDFs remain in scope even though standalone image import does not. Their pages are rendered locally and passed through the same internal OCR component.

## 5. Chosen control-plane approach

The repository will use the following control-plane documents:

```text
AGENTS.md                         Canonical operating contract for all agents
CLAUDE.md                         Claude entry point and critical safety summary
README.md                         Recruiter-facing landing page

docs/
  specs/
    recruiter-vertical-slice.md  Canonical behavior for the active milestone
  tasks/
    README.md                    Task schema and status rules
    CB-###-*.md                  One bounded task contract per file
  ARCHITECTURE.md                Components, trust boundaries, and data flow
  THREAT_MODEL.md                Assets, attackers, abuse cases, and residual risks
  ROADMAP.md                     Milestone order and phase gates
  DECISIONS.md                   Short architecture decision records
  PROGRESS.md                    Completed work and actual verification output
  REVIEW_QUEUE.md                Batched, non-blocking human review items
  BLOCKERS.md                    Reproductions and exhausted attempts
  METRICS.md                     Accuracy, safety, latency, memory, and FP results
  research/README.md             Index of non-authoritative background documents
```

The large existing research documents need not be physically moved during the control-plane change. `docs/research/README.md` will classify and link them as non-authoritative. Physical relocation may occur later only after all references are updated and verified.

## 6. Document authority

When documents conflict, agents must use this order:

1. `AGENTS.md` governs the work process, immutable safety properties, and agent permissions.
2. `docs/specs/recruiter-vertical-slice.md` governs product behavior and scope for the active milestone.
3. `TECH_SPEC.md` governs lower-level implementation decisions that do not conflict with items 1-2.
4. `DESIGN_SPEC.md` governs interaction design, accessibility, and visual language.
5. `docs/ROADMAP.md` governs milestone order and phase gates.
6. `docs/cashguard_prd_v0.2.md` provides historical product background and future direction.
7. Research documents provide context only and never authorize implementation.

`TASKS.md` becomes a historical planning input. Active execution is governed by `docs/ROADMAP.md` and the individual files in `docs/tasks/`.

If a true contradiction remains after applying this order, the agent must choose the smallest reversible option that preserves all safety invariants, record the choice in `docs/DECISIONS.md`, and add a `should-review` item. It must stop for human review only when the contradiction affects security, privacy, money semantics, external data transmission, signing/entitlements, or irreversible user data.

## 7. AGENTS.md design

`AGENTS.md` is the canonical operating contract. It will contain:

1. Project thesis and current milestone.
2. Document authority.
3. Commands and environment facts.
4. Permanent architecture boundaries.
5. Immutable financial, security, privacy, and model-authority invariants.
6. Task selection and execution loop.
7. Test-first and verification requirements.
8. Dependency policy.
9. Logging and sensitive-data rules.
10. Failure, blocker, and escalation protocols.
11. Change-scope and repository-hygiene rules.
12. Definition of done.

It must be concise enough for an agent to load on every task. It will link to detailed documents rather than duplicating them.

## 8. CLAUDE.md design

`CLAUDE.md` will be a short compatibility entry point rather than an independent operating manual. It will:

- require reading `AGENTS.md` before acting;
- state the active milestone and document authority;
- repeat only the small set of critical safety rules whose omission would be dangerous;
- point to the current task contract and milestone specification; and
- avoid separate task loops or fallback matrices that could drift from `AGENTS.md`.

The intentionally repeated safety rules are:

- deterministic code owns money, risk, policy, and canonical state;
- the model cannot approve, lower risk, replace verified contact data, or mutate trusted state;
- document text is untrusted;
- document bytes and extracted text never cross a network boundary in milestone 1;
- money is fixed-point integer cents;
- tests must run without Foundation Models; and
- no new dependency is allowed without a recorded decision and human review.

## 9. Lower-cost agent operating model

Agents execute one bounded task per run. They do not select broad features or reinterpret the product.

Every task contract contains:

```text
ID and status
Objective
Why this task exists
Required reading
Allowed files
Inputs and outputs
Invariants
Implementation notes already decided
Tests to add or update
Verification commands
Manual checkpoint, if any
Explicit non-goals
Definition of done
Handoff notes
```

Task status is one of `planned`, `ready`, `in_progress`, `blocked`, or `done`. Only a human or the completion of all prerequisite gates may move a task to `ready`. An agent may move its assigned task from `ready` to `in_progress`, `blocked`, or `done`; it may not mark unrelated tasks ready.

Agents must:

1. Read `AGENTS.md`, the assigned task, and only the referenced detailed sections.
2. Inspect the current implementation before proposing edits.
3. Restate the task and invariants in `docs/PROGRESS.md`.
4. Add or update the required tests before deterministic business logic.
5. Implement the smallest change satisfying the contract.
6. Run the exact verification commands.
7. Record actual pass/fail counts and relevant measurements.
8. Self-review against the task and permanent invariants.
9. Leave a concise handoff and stop.

Agents must not:

- expand product scope;
- change an invariant to make implementation easier;
- weaken, delete, skip, or quarantine a failing safety test;
- introduce a dependency or network path without authorization;
- perform financial arithmetic in prompts or model-generated code paths;
- use raw document text as trusted explanation or verification content;
- claim a task is complete without fresh verification output; or
- edit files outside the task's allowed-file list unless a build-generated file is explicitly named.

After three genuinely different failed approaches, the agent records a minimal reproduction, hypotheses, attempts, and next action in `docs/BLOCKERS.md`. It must not repeat cosmetic variations of the same failed approach.

## 10. Platform and module architecture

CashBooks is an iOS product. A minimal macOS build exists only to validate live Foundation Models behavior on compatible hardware. Full macOS feature parity is not required.

```text
CashBooks app targets
  File import and review UI
  PDFKit/Vision document pipeline
  Foundation Models adapter
  SwiftData persistence adapter
  Platform diagnostics and accessibility
                |
                v
CashBooksCore Swift package
  Money and domain value types
  Document and provenance value types
  Deterministic validators
  Payment fingerprint engine
  Risk policy engine
  Rule-based extraction fallback
  Synthetic and adversarial fixtures
```

`CashBooksCore` must compile and test without importing Foundation Models, PDFKit, Vision, SwiftUI, SwiftData, or networking frameworks. Platform adapters convert framework-specific values into core DTOs.

Foundation Models code may depend on core types. Core code must never depend on Foundation Models code.

## 11. Document-ingestion data flow

```text
Security-scoped PDF URL
  -> validated DocumentArtifact
  -> per-page PDFKit text extraction
     OR high-quality page render plus Vision OCR fallback
  -> ExtractedPage records with method and quality metadata
  -> quarantined UntrustedDocument
  -> Foundation Models or rule-based DraftExtractor
  -> deterministic field validation
  -> payment fingerprint comparison
  -> deterministic RiskDecision with evidence IDs
  -> owner review and correction
  -> confirmed local canonical record
```

Acquisition, document text extraction, semantic draft extraction, validation, and persistence are separate stages with explicit interfaces.

### 11.1 Document validation

Before parsing, the app validates:

- selected Uniform Type Identifier;
- PDF magic/header and PDFKit readability;
- configurable byte and page limits;
- encryption and locked state;
- duplicate content hash; and
- cancellation state.

Passwords are accepted interactively, held only as long as required to unlock the document, and never persisted or logged.

### 11.2 Per-page extraction

Each page first attempts embedded-text extraction. An explicit quality policy evaluates character count, printable-character ratio, replacement characters, and whitespace quality. A page that fails uses a local raster render and Vision OCR.

Every extracted page records:

- page index;
- extraction method (`pdfTextLayer` or `visionOCR`);
- raw quarantined text;
- normalized text;
- extraction-quality metadata; and
- optional source regions when the framework provides reliable geometry.

### 11.3 Semantic extraction

The model receives bounded, clearly delimited untrusted text in a tool-less session and produces only a typed draft. Deterministic scanners run across the complete normalized document independently of model output.

The rule-based implementation supports the same draft protocol and is always available. Model refusal, unavailability, invalid schema, validation failure, or context-limit failure routes to the rule-based path without changing safety behavior.

### 11.4 Canonicalization

No extraction path writes a canonical invoice or payment request directly. The owner must review and confirm material fields. Low-confidence or conflicting fields remain incomplete or conflicting; the app does not guess silently.

## 12. Immutable safety and correctness properties

The following are release-blocking invariants:

1. Deterministic code is the sole authority for money, risk, policy, trusted contacts, and canonical state.
2. Model influence is monotone with respect to safety: it may add risk signals but cannot clear or reduce deterministic ones.
3. Untrusted-document extraction sessions have no tools or mutation capabilities.
4. Document-provided phone numbers, email addresses, URLs, and instructions cannot replace stored verification contacts.
5. Raw document text may be displayed only through visibly quarantined UI.
6. Every material extracted field has evidence lineage to a source document and page.
7. Financial values use fixed-point integer cents; `Double` is forbidden for money.
8. No financial arithmetic is delegated to a model.
9. Tests and core functionality do not require Foundation Models availability.
10. Milestone 1 contains no document-upload endpoint and no document network request.
11. Passwords, raw account details, document contents, and model prompts do not appear in logs or analytics.
12. A failure or cancellation cannot leave a partial canonical financial record.

## 13. Error handling

The system uses typed errors grouped by stage: acquisition, document validation, unlocking, PDF parsing, page rendering, OCR, semantic extraction, field validation, fingerprint evaluation, and persistence.

Expected behavior includes:

- unsupported, oversized, corrupt, empty, and unreadable documents fail before canonicalization;
- locked PDFs request a password or exit without side effects;
- OCR uncertainty creates a visibly incomplete draft;
- conflicting totals, dates, vendors, or payment instructions require owner resolution;
- model errors route to deterministic fallback;
- cancellation deletes temporary artifacts and does not create canonical records;
- duplicate hashes warn before re-import; and
- user-facing errors omit sensitive diagnostics while development diagnostics remain redacted.

## 14. Test and evaluation strategy

### 14.1 Pure deterministic tests

- money sign and rounding properties;
- date and clock injection;
- draft validation;
- payment fingerprint equality and mismatch behavior;
- risk-signal accumulation;
- evidence-ID integrity;
- trusted-contact source restrictions; and
- fallback behavior.

### 14.2 Document fixtures

The fixture corpus includes:

- digitally generated text PDF;
- image-only scanned PDF;
- OCRed hybrid PDF;
- mixed text and scanned pages;
- rotated page;
- table-heavy invoice;
- multi-page invoice with late totals or payment instructions;
- blank document;
- duplicate document;
- password-protected document;
- incorrect password;
- malformed or truncated document;
- oversized document;
- conflicting totals or dates;
- OCR-confusable characters; and
- unsupported or low-confidence content.

### 14.3 Adversarial and metamorphic tests

- adding prompt-like instructions cannot reduce risk;
- removing or reordering irrelevant prose cannot change canonical monetary fields;
- model output cannot replace stored verification contact data;
- a payment fingerprint mismatch remains present when the model reports no change;
- OCR noise cannot silently create a confirmed high-confidence amount;
- corrupt input cannot create a partial canonical record; and
- model-available and model-unavailable paths preserve the same safety properties.

### 14.4 Manual platform checks

- live Foundation Models behavior on compatible Mac hardware;
- model-unavailable behavior on an ineligible device or controlled configuration;
- VoiceOver;
- Dynamic Type;
- Reduce Motion;
- color contrast and non-color risk cues;
- security-scoped file lifecycle; and
- cancellation during extraction.

### 14.5 Required measurements

Measurements are reported with named hardware and OS versions:

- per-field exact-match extraction accuracy;
- OCR failure rate;
- validation-rejection rate;
- deterministic-fallback rate;
- risk false positives and false negatives on the labeled corpus;
- safety downgrade count;
- cold and warm p50/p95 document-processing latency;
- model response latency; and
- peak memory for representative multi-page PDFs.

No target number will be invented in advance solely to make the project look successful. Baselines are recorded first; budgets are selected from observed performance and product requirements.

## 15. First-milestone exit gate

The recruiter-first milestone is complete only when:

- all deterministic tests pass without Apple Intelligence;
- the adversarial corpus has zero safety downgrades;
- no test or runtime path uploads document bytes or extracted text;
- every material draft field displayed in the hero flow exposes evidence lineage;
- model failure demonstrably preserves the deterministic safety outcome;
- accuracy and performance measurements are recorded in `docs/METRICS.md`;
- accessibility and failure-state checkpoints are recorded;
- the app can reset and replay the hero scenario reproducibly; and
- a 60-90 second demo shows import, evidence, changed payment details, trusted-contact verification, and fallback.

## 16. Naming migration

The canonical product name is CashBooks. Migration will be deliberate rather than a blind text replacement:

1. Establish CashBooks in the control-plane documents and README.
2. Rename the Xcode project, targets, schemes, bundle identifiers, source directories, test targets, and visible product strings in a dedicated task.
3. Rename `CashGuardKit` concepts to `CashBooksCore` in a separate, build-gated task when the package exists.
4. Update active specification references.
5. Leave historical research quotations unchanged where renaming would alter source material; label them as historical context.
6. Verify iOS and macOS builds and test discovery after each structural rename.

No agent may combine the Xcode rename with feature implementation.

## 17. Implementation sequencing after this design

After this written design is approved, a detailed implementation plan will sequence:

1. Control-plane files and authority cleanup.
2. Repository and Xcode naming migration.
3. CashBooksCore package shell and verification commands.
4. Core money, evidence, document, and risk types.
5. Deterministic policy and fingerprint engines.
6. PDF acquisition and validation.
7. PDFKit text extraction and Vision OCR fallback.
8. Draft extraction protocol and rule-based fallback.
9. Foundation Models adapter and validation.
10. Review UI, provenance UI, and local persistence.
11. Adversarial corpus and model regression harness.
12. Performance, accessibility, privacy, and recruiter-demo artifacts.

Plaid Sandbox and the Share Extension remain separate later milestones.

## 18. Design risks and mitigations

### Documentation becomes another substitute for code

Mitigation: the control-plane change is one bounded milestone. After its gate passes, work moves immediately to a runnable vertical slice. New documentation must either govern a decision, capture evidence, or support recruiter comprehension.

### Lower-cost agents follow stale documents

Mitigation: `AGENTS.md` and each task contract state the authority order. Historical files are labeled non-authoritative for the active milestone.

### Rules are duplicated between AGENTS.md and CLAUDE.md

Mitigation: only the small release-blocking safety subset is intentionally repeated. All procedural details live in `AGENTS.md`.

### Local-only claims become inaccurate

Mitigation: milestone 1 is genuinely local for document processing and has no backend. Later Plaid work must use the phrase local-first and document its network boundary precisely.

### The app becomes a broad accounting clone

Mitigation: the active specification and task contracts explicitly exclude accounting-system features. CashBooks is positioned as an independent pre-payment trust layer.

### Model integration dominates the architecture

Mitigation: the core package has no Foundation Models dependency, deterministic fallback is always available, and model output has no financial authority.

## 19. Acceptance criteria for the control-plane milestone

The control-plane groundwork itself is complete when:

- `AGENTS.md` exists and is the single canonical operating contract;
- `CLAUDE.md` is a concise compatible entry point with no independent conflicting workflow;
- the project is named CashBooks in active documents;
- the active milestone specification, roadmap, architecture, and threat model agree;
- every referenced control-plane path exists;
- the initial task queue uses the required bounded-task schema;
- existing PRD and research documents are clearly classified as background or future scope;
- no broken active-document references remain;
- a lower-cost agent can select the first `ready` task without making a product or architecture decision; and
- no application feature code is mixed into the control-plane change.
