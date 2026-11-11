Source: docs/superpowers/specs/2026-08-31-cashbooks-multi-model-agent-system-design.md
Tag: archive/control-plane-2026-11
Phase, gate, and capability-class wording in the copied text is historical.

## 36. CashBooks application boundary

The application architecture remains:

~~~text
Future SwiftUI client (deferred)
  consumes immutable commands and snapshots only
                  |
                  v
CashBooksLocalEngineHost
  headless operation coordinator
  PDFKit/Vision adapters
  Foundation Models adapter
  SwiftData adapter
  privacy-aware diagnostics and runtime network sentinel
  XCTest-facing integration API
  minimal macOS live-model harness
                  |
                  v
CashBooksCore Swift package
  Money and domain value types
  Document and provenance DTOs
  deterministic validators
  payment fingerprint engine
  risk policy engine
  rule-based extraction fallback
  synthetic normalized fixtures
~~~

CashBooksCore must compile and test without:

- Foundation Models;
- PDFKit;
- Vision;
- SwiftUI;
- SwiftData;
- networking frameworks.

Framework adapters convert platform values into immutable, Sendable Core DTOs. Foundation Models-specific generated types remain in the app adapter.

The current milestone may leave the generated SwiftUI shell in place so iOS
and macOS targets continue to build, but feature views do not participate in
G3. A future client may only:

- submit explicit engine commands;
- observe immutable operation, review, and result snapshots;
- supply user corrections with evidence provenance; and
- request confirmation authorization through a trusted host boundary.

It may not call PDFKit, Vision, Foundation Models, or SwiftData directly.

### 39.2 Platform decision

Milestone one supports:

- iOS as the eventual user-product target and a platform-integration host;
- macOS as the live Foundation Models validation harness and a second
  platform-integration host.

The current milestone does not claim a complete user product on either
platform. It proves the local engine behind the future iOS experience.

visionOS is removed from the milestone-one support matrix. It may be reconsidered only after the recruiter release and only with an explicit product reason.

### 39.3 Concurrency decision

- PDFDocument and PDFPage are owned by a dedicated document actor.
- Vision work is actor- or request-owned with bounded page concurrency.
- Foundation Models sessions are per request or owned by a dedicated adapter actor.
- SwiftData uses a ModelActor-backed repository.
- The headless operation coordinator exposes immutable Sendable snapshots.
- Core DTOs crossing actor boundaries are immutable and Sendable.
- Framework objects and SwiftData model instances do not cross actors.

Future SwiftUI isolation is a UI-program decision. It is not a G1 or G3 gate.

### 40.1 Foundation tasks

| ID | Contract | Dependencies | Initial capability |
|---|---|---|---|
| P2-A1 | Money, currency, rounding | G1 | standard implementation, complex review |
| P2-A2 | Evidence IDs and lineage | G1 | standard implementation, complex review |
| P2-A3 | Injected clock and stable identifiers | G1 | standard implementation |
| P2-B | Synthetic corpus and fixture specification | G1 | mechanical execution, standard review |
| P2-M0 | Installed Foundation Models SDK reconnaissance and feasibility ADR | G1 | complex implementation |
| P2-C | DocumentArtifact, ExtractedPage, UntrustedDocument, draft DTOs | P2-A1, P2-A2 | standard implementation |
| P2-D | Fingerprint, trusted contact, risk signal and decision DTOs | P2-A1, P2-A2 | standard implementation, complex security review |
| P2-E | Document, draft-extractor, validator, fingerprint, and risk protocols | P2-C, P2-D | complex implementation |
| P2-F | ConfirmDraftCommand and atomic persistence contracts | P2-C, P2-D | complex implementation |
| P2-G | Headless operation, review-snapshot, result-snapshot, and confirmation-authorization contracts | P2-C through P2-F | complex implementation |
| P2-H | Protected invariant and metamorphic test packs | P2-C through P2-G | independent complex verifier |

ConfirmDraftCommand is a frozen, Sendable Core value. It contains:

- a command ID and draft ID;
- the expected draft version hash;
- confirmed canonical values, including explicit corrections;
- evidence IDs and correction-provenance references for every material field;
- the risk-decision and trusted-contact assessment versions reviewed by the
  caller;
- a signed, caller-supplied ConfirmationAuthorization envelope; and
- the expected repository revision needed for stale and replay checks.

ConfirmationAuthorization is a cryptographically verifiable capability, not a
Boolean assertion or arbitrary string. Its signed payload is bound to:

- authorization ID and command ID;
- draft ID and exact draft-version hash;
- reviewed risk-decision and trusted-contact-assessment versions;
- a canonical hash of the confirmed values and evidence references;
- issuer identity;
- issuance and expiration instants; and
- a unique nonce.

Production authorization uses a per-install signing key held outside
CashBooksCore in the trusted local host. The envelope may be transported as a
Core value, but it has no public production initializer that can mint a valid
signature. A verifier-owned test target uses a distinct injected test issuer;
production code cannot load that issuer or key.

P3-H3 owns signature and expiry verification. The SwiftData confirmation
transaction records authorization consumption in the same atomic transaction
as the canonical aggregate. It rejects an invalid signature, wrong binding,
expired capability, already-consumed authorization, stale repository
revision, or divergent replay. A byte-for-byte replay of an already committed
command returns the original result snapshot without another write.

A document extractor, draft extractor, Foundation Models adapter, risk engine,
or ordinary caller cannot mint a valid authorization. Missing or invalid
authorization never creates partial canonical state.

## 41. P3: Parallel subsystem implementation

### 41.1 Document lane

~~~text
Frozen document contracts
  ├── P3-D1 security-scoped acquisition and staging
  ├── P3-D2 UTI/header/size/page/encryption/hash validation
  ├── P3-D3 PDFKit embedded-text adapter
  ├── P3-D4 pure page-text quality policy
  ├── P3-D5 PDF page raster adapter
  └── P3-D6 Vision OCR recognizer
                         ↓
                P3-D7 hybrid coordinator
~~~

Rules:

- copy the security-scoped source into app-controlled staging while access is active;
- release the scope after the copy;
- keep an unlock password only for the active operation and never persist, log,
  include in telemetry, or pass it to a model;
- one actor owns PDFDocument;
- pass only Sendable page snapshots downstream;
- bound OCR page concurrency;
- restore deterministic page order;
- check cancellation between expensive operations;
- use an import generation ID to reject late results;
- use cleanup and autorelease boundaries to control memory.

### 41.2 Trust and risk lane

~~~text
Frozen risk contracts
  ├── P3-T1 fingerprint construction and comparison
  ├── P3-T2 deterministic complete-text scanners
  ├── P3-T3 pure signal scoring and monotone merge
  └── P3-T4 stored-contact verification and repin ledger policy
                         ↓
                P3-T5 risk composition
~~~

The signal catalog, weights, caps, thresholds, and monotonicity rule are one semantic unit. The risk engine consumes a fingerprint assessment; it does not read SwiftData or reproduce fingerprint lifecycle logic.

### 41.3 Deterministic extraction lane

~~~text
P3-E1 DraftValidator
  → P3-E2 RuleBasedDraftExtractor
  → P3-E3 failure and availability-neutral fallback router
~~~

This lane consumes normalized Core documents, so it does not depend on working PDFKit or Vision adapters.

### 41.4 Persistence lane

~~~text
P3-P1 Core repository and confirmation command contract
→ P3-P2 SwiftData schema and migration baseline
→ P3-P3 ModelActor repository
→ P3-P4 atomic, idempotent confirmation transaction
→ P3-P5 failure-injection and cancellation tests
~~~

One task owns schema records, relationships, Schema declarations, and migration baseline. After schema v1 freezes, a change requires an exclusive migration task.

One confirmation transaction persists:

- confirmed canonical record;
- field evidence links;
- risk decision and signals;
- audit event;
- approved fingerprint transition.

The transaction accepts only a ConfirmDraftCommand whose
ConfirmationAuthorization has been verified through P3-H3. Inside the same
SwiftData transaction, it rechecks the signed bindings and records the
authorization ID as consumed. It rejects a missing or invalid authorization,
expired capability, stale draft hash, stale reviewed-risk version, unresolved
material evidence, already-consumed capability, or replay with divergent
content. A replay of the exact same committed command is idempotent and
returns the original result snapshot.

Protected tests cover:

- no authorization means no write;
- corrected values retain both source and correction provenance;
- exact replay is idempotent;
- forged, expired, wrong-draft, and wrong-command authorizations are rejected;
- divergent reuse of a command ID is rejected;
- authorization consumption and aggregate persistence are atomic;
- cancellation and injected failures leave no partial aggregate; and
- success commits the complete aggregate atomically.

### 41.5 Headless operation lane

~~~text
P2-G frozen command and snapshot contracts
  → P3-H1 pure operation-state reducer using fakes
  → P3-H2 actor-owned local-engine coordinator
  → P3-H3 signed confirmation issuer and verifier boundary
~~~

The UI-neutral operation state machine includes:

~~~text
idle
→ acquiring
→ validatingDocument
→ awaitingPassword, when required
→ extracting
→ drafting
→ validatingDraft
→ awaitingConfirmation
→ persisting
→ completed
~~~

failed and canceled are terminal for an attempt.

Required race tests:

- import B cancels import A and rejects A's late completion;
- no automatic path advances from awaitingConfirmation to persisting;
- confirmation cannot create two aggregates;
- cancellation during OCR creates no canonical record;
- model failure preserves deterministic risk and routes to fallback;
- imported contact data never becomes the trusted-contact value;
- PDF passwords are cleared after unlock or failure.

The coordinator publishes immutable snapshots. It does not know how a future
screen is rendered and does not import SwiftUI.

### 41.6 Foundation Models lane

~~~text
P2-M0 installed-SDK reconnaissance and accepted feasibility ADR
→ P3-M3 app-local generated response type
→ P3-M4 tool-less session adapter
→ P3-M5 generated response to Core draft mapping
→ P3-M6 availability, refusal, invalid-output, and cancellation routing
→ P3-M7 headless availability router and macOS live-model harness
~~~

Rules:

- generated types stay outside Core;
- the adapter cannot import persistence or own risk authority;
- sessions are not shared across simultaneous imports;
- automated tests use a fake session client;
- live model behavior is measured separately;
- model failure preserves deterministic fallback semantics.

Two independent evidence classes are required:

1. deterministic fake-session and rule-fallback tests that pass on every
   supported development machine without Apple Intelligence; and
2. live local-model compatibility evidence from the declared supported macOS
   harness before the repository may claim live Foundation Models support.

A missing live-capable machine does not fail G3-B, but it blocks G3-L and every
claim that CashBooks currently supports live Foundation Models.

### 41.7 Fixture and evaluation lane

Three distinct layers:

~~~text
Platform fixtures
  synthetic PDF binaries → PDFKit/Vision → ExtractedPage

Core fixtures
  normalized extracted-document JSON → validator/risk

End-to-end fixtures
  controlled PDF import → immutable review snapshot
  → explicit harness confirmation → atomic persistence
~~~

Synthetic variants include:

- digital text;
- image-only scan;
- OCRed hybrid;
- mixed pages;
- rotated scan;
- table-heavy invoice;
- multi-page late totals;
- blank;
- duplicate;
- locked;
- incorrect password;
- malformed or truncated;
- oversized;
- conflicting totals and dates;
- OCR-confusable characters;
- changed payment instructions;
- fake contact data;
- prompt-like instructions.

PDF binaries belong to app/platform test resources. CashBooksCore receives normalized JSON, not PDFKit resources.

After subsystem correctness, the current milestone adds:

| ID | Deliverable | Dependencies | Initial capability |
|---|---|---|---|
| P3-Q1 | Adversarial and metamorphic regression | document, extraction, trust/risk | complex verifier with mechanical corpus expansion |
| P3-Q2 | Extraction/risk metrics and confusion matrix | P3-Q1 | standard implementation |
| P3-Q3 | Cold/warm latency and peak-memory measurements | headless coordinator | complex implementation |
| P3-Q4 | Privacy-aware logging and runtime network sentinel | document and engine host | complex implementation |
| P3-Q5 | Replayable headless runbook, architecture diagram, metrics, and limitations | P3-Q1 through P3-Q4 | standard plus mechanical execution |
| P3-I | Headless iOS/macOS integration and failure injection | all P3 lanes | complex integrator |

Only the metrics aggregator writes the generated metrics summary. Workers emit
unique measurement artifacts.

### 41.8 G3 headless local-intelligence-engine gates

The headless pipeline must run:

~~~text
synthetic PDF
→ local page extraction
→ quarantined document
→ rule-based or typed Foundation Models draft
→ deterministic field validation
→ payment fingerprint comparison
→ risk decision with evidence
→ immutable review snapshot
→ deliberate caller confirmation
→ atomic local persistence
→ immutable result snapshot
~~~

#### 41.8.1 G3-B baseline

G3-B passes only when:

- all named PDF paths are exercised;
- no network operation can observe document bytes or extracted text;
- prompt-like text cannot reduce risk;
- imported contacts cannot change trusted verification contacts;
- every material field carries resolvable evidence;
- deterministic fake-session and fallback suites pass without Foundation Models;
- availability, refusal, malformed-output, cancellation, and context-limit
  paths preserve deterministic safety;
- the Foundation Models adapter compiles and its fake-session mapping,
  refusal, invalid-output, and fallback behavior pass without a live model;
- extraction and model paths cannot mint confirmation authorization;
- no confirmation produces no canonical record;
- stale and divergent confirmations are rejected;
- forged, expired, wrong-draft, wrong-command, and consumed confirmation
  authorizations are rejected;
- exact replay is idempotent;
- corrected fields retain source and correction provenance;
- cancellation and failure create no partial canonical record;
- successful confirmation persists the complete aggregate atomically;
- iOS and macOS platform builds and headless integration suites pass;
- latency and memory evidence identifies hardware, OS, fixtures, sample size,
  and method;
- Core, adapter, actor, privacy, and network boundaries remain intact;
- the headless runbook replays from a clean local state; and
- Sol completes the G3-B invariant audit and the human accepts the evidence.

G3-B closes the currently authorized implementation program and permits a new
UI-design brainstorm because the stable engine boundary exists. It permits
only the claim that Foundation Models integration is implemented and
deterministically tested against fakes; it does not permit a live-model support
claim.

#### 41.8.2 G3-L live-model-supported claim

G3-L is an additive evidence claim, not a prerequisite for G3-B. It requires:

- P3-M7 to run on the declared compatible macOS, Xcode, OS, hardware, language,
  region, and Apple Intelligence configuration;
- live structured generation to map through the same untrusted Core draft
  boundary;
- availability, refusal, malformed-output, cancellation, and context-limit
  observations to preserve deterministic safety and fallback;
- no document bytes or extracted text to cross a network boundary;
- a signed harness report naming the configuration, fixture IDs, run time, and
  limitations; and
- human acceptance of the evidence before README, resume, demo, or recruiter
  material claims live Foundation Models support.

If compatible live execution is unavailable or fails, G3-B remains valid and
CashBooks remains fully testable through its deterministic fallback, but G3-L
stays open.

Neither G3-B nor G3-L proves product usability,
visual quality, accessibility presentation, or recruiter-demo readiness.

