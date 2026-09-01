# CashBooks Multi-Model Engineering System

**Status:** Human-approved headless-engine scope; reviewed canonical design  
**Date:** 2026-08-31  
**Owner:** Himanshu Jain  
**Architectural authority:** GPT-5.6 Sol, with human approval  
**Scope:** Development-time orchestration plus implementation architecture for the CashBooks headless local-intelligence engine  
**Application-code changes authorized by this document:** None

## 1. Executive decision

CashBooks will be built by a deterministic, vendor-neutral engineering control plane around bounded model workers. It will not use a free-form agent swarm. The currently authorized product milestone is the non-UI local-intelligence engine, not the finished iOS experience.

The control plane owns:

- task state;
- dependency resolution;
- path and semantic locks;
- leases and concurrency;
- budgets and provider limits;
- verification execution;
- reviewer independence;
- integration eligibility;
- durable event history; and
- human approval gates.

Models may propose plans, implement scoped changes, diagnose evidence, review candidates, and prepare integration artifacts. Models may not grant themselves permissions, change scheduler state directly, approve their own work, waive a failed test, or merge without evidence.

The initial model allocation is:

| Responsibility | Initial model |
|---|---|
| Global architecture, invariants, phase audits, severe escalation | GPT-5.6 Sol |
| Work-package decomposition and evidence-driven replanning | Claude Opus |
| Complex Swift implementation and integration | GPT-5.6 Terra |
| Standard implementation and routine review | Claude Sonnet 5 |
| Mechanical, tightly bounded execution | Gemini |

This allocation is an initial prior. The router will eventually use measured success, risk, review burden, latency, and cost per accepted task.

The active implementation-planning boundary ends at a headless G3 pipeline:

~~~text
local PDF
→ PDFKit/Vision extraction
→ typed untrusted draft through rule-based or Foundation Models adapters
→ deterministic validation, risk, and evidence
→ immutable review snapshot
→ deliberate external confirmation command
→ atomic SwiftData persistence
→ immutable result snapshot
~~~

The current milestone defines stable operation and snapshot contracts for a future client, but it does not authorize SwiftUI views, navigation, design tokens, accessibility presentation, Files-picker composition, animation, or UI automation. Those belong to a separately approved UI product program after G3.

## 2. Relationship to earlier CashBooks work

This design supersedes the execution and orchestration model in:

- docs/superpowers/specs/2026-08-30-cashbooks-control-plane-design.md; and
- docs/superpowers/plans/2026-08-31-cashbooks-control-plane.md.

It does not discard their approved product architecture. The following decisions remain authoritative:

- CashBooks is the canonical product name.
- The eventual first release is a recruiter-first vertical slice, not a QuickBooks clone.
- The active milestone is its headless local-intelligence engine foundation.
- iOS is the product; macOS is a minimal live Foundation Models validation harness.
- CashBooksCore is deterministic and framework-independent.
- Synthetic fixtures precede Plaid.
- An in-app Files importer precedes a Share Extension.
- PDFKit embedded text extraction precedes Vision OCR fallback per page.
- Every material extracted field retains evidence lineage.
- Foundation Models produces typed, untrusted drafts.
- Deterministic code remains authoritative for money, policy, risk, trusted contacts, and canonical persistence.
- Model influence on safety is monotone: it may add risk but may not reduce deterministic risk.
- A rule-based extractor remains available without Apple Intelligence.
- Milestone-one document bytes and extracted text never cross a network boundary.
- Plaid, backend synchronization, Share Extension, standalone image import, and full accounting remain deferred.

The earlier serial task plan remains a useful inventory of acceptance criteria and likely file paths. It is historical planning input, not the active scheduler.

## 3. Why the orchestration model changed

The former plan allowed only one ready or active task. That is safe but wastes the natural independence between:

- deterministic risk logic;
- PDF acquisition and extraction;
- semantic draft extraction;
- synthetic fixture construction;
- SwiftData persistence;
- headless operation coordination built against fakes; and
- Foundation Models adapter research.

Blindly allowing all tasks to run together would be worse. Shared DTOs, Xcode configuration, package manifests, SwiftData schemas, financial invariants, and workflow state can conflict without touching the same file.

The replacement therefore uses:

1. contract-first design;
2. explicit dependency DAGs;
3. isolated worktrees;
4. file write sets;
5. semantic resource locks;
6. independent verification;
7. topological integration; and
8. bounded escalation.

The governing rule is:

> Parallelize implementation behind frozen interfaces; never parallelize interface invention.

## 4. Goals

### 4.1 Engineering goals

- Permit genuinely independent CashBooks tasks to execute concurrently.
- Preserve a deterministic and inspectable control plane.
- Make work resumable across providers, sessions, and context resets.
- Keep expensive models out of routine execution.
- Prevent one model from planning, implementing, and approving the same change.
- Make every completion claim traceable to reproducible evidence.
- Support manual dispatch first and provider automation later without changing task contracts.
- Keep application privacy guarantees separate from development-tool implementation choices.
- Generate a project history that demonstrates senior-level systems judgment to recruiters.

### 4.2 Quality goals

- Detect task drift, test tampering, stale contexts, repeated failures, and unsafe integration.
- Protect CashBooks financial, privacy, evidence, and model-authority invariants.
- Measure model performance by task class rather than relying permanently on assumed rankings.
- Preserve clean builds and recoverable repository state after interruption.
- Keep application behavior testable without a live Foundation Models runtime.
- Deliver the complete local document-to-persistence behavior without requiring a product UI.
- Preserve a narrow, immutable command-and-snapshot boundary for the future SwiftUI client.

### 4.3 Cost goals

- Use Sol only for architecture, phase gates, and severe ambiguity.
- Use Opus at work-package boundaries instead of as an always-on manager.
- Route routine volume to Sonnet and bounded mechanical work to Gemini.
- Use Terra for complex implementation, integration, and risk-driven escalation.
- Optimize cost per accepted task, not price per call.
- Stop retries that add no new evidence.

## 5. Non-goals

This design does not:

- build a general-purpose autonomous-agent platform;
- permit unbounded model-to-model conversation;
- use majority vote as proof of code correctness;
- automate product-scope or invariant changes;
- allow workers to access real customer documents;
- add cloud inference to the CashBooks product;
- implement Plaid, a backend, a Share Extension, or standalone image ingestion;
- implement SwiftUI screens, navigation, visual design, design tokens, animation,
  UI accessibility presentation, or UI automation in the active milestone;
- claim that G3 is a finished recruiter demo or shippable end-user product;
- require every model provider to expose the same tools;
- eliminate human approval for consequential decisions; or
- change CashBooks application source during the design milestone.

## 6. Design evidence and principles

The architecture applies five recurring patterns:

1. **Planning:** hierarchical decomposition from architecture to work package to task.
2. **Tool use:** narrow, typed capabilities with least privilege.
3. **Reflection:** bounded repair driven by compiler, test, diff, or reviewer evidence.
4. **Multi-agent collaboration:** specialized roles with artifact-based handoffs.
5. **Evaluation:** component and end-to-end measurements that update routing decisions.

Relevant practitioner and research guidance includes:

- Andrew Ng / DeepLearning.AI agentic design patterns: https://www.deeplearning.ai/the-batch/how-agents-can-improve-llm-performance/
- Anthropic, Building Effective Agents: https://www.anthropic.com/engineering/building-effective-agents
- Anthropic, Effective Context Engineering for AI Agents: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Anthropic, Effective Harnesses for Long-Running Agents: https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- Stanford AgentFlow: https://agentflow.stanford.edu/
- Stanford CS224G orchestration lecture: https://web.stanford.edu/class/cs224g/lectures/CS%20224G%202026%20Lecture%207%20-%20Agent%20Orchestration%20%26%20Workflow%20Design.pdf
- OpenAI, A Practical Guide to Building AI Agents: https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/
- FrugalGPT cost-quality cascades: https://arxiv.org/abs/2305.05176
- MAST multi-agent failure taxonomy: https://arxiv.org/html/2503.13657v3

These sources motivate the design but do not substitute for CashBooks-specific evaluation. Multiple agents can increase cost, context loss, coordination failures, and correlated mistakes. Parallelism must be earned by independence.

## 7. Alternatives considered

### 7.1 Free-form swarm

Agents dynamically delegate and converse until they believe the objective is complete.

**Advantages**

- flexible decomposition;
- visually impressive;
- low up-front scheduler design.

**Rejected because**

- authority becomes ambiguous;
- stop conditions are unreliable;
- duplicate work and circular reviews are common;
- chat history becomes accidental state;
- cost is difficult to bound;
- a persuasive model can mask weak evidence.

### 7.2 Fixed model assembly line

Every task passes through the same planner, implementer, reviewer, and merger sequence.

**Advantages**

- simple;
- predictable;
- easy to audit.

**Rejected as the sole model because**

- it serializes independent work;
- it routes trivial work through expensive stages;
- it handles failures and cross-cutting work poorly;
- it cannot adapt to task-specific risk or provider capability.

### 7.3 Deterministic DAG with bounded specialists

Code determines state and authority. Models operate only inside typed work packets.

**Advantages**

- safe concurrency;
- measurable routing;
- resumability;
- vendor neutrality;
- explicit cost and failure controls;
- clean separation between stochastic generation and deterministic authority.

**Cost**

- more up-front contract and tooling work;
- integration and semantic locks require deliberate maintenance;
- task authors must define objective acceptance evidence.

**Decision:** Adopt this approach.

## 8. System context

~~~text
Human owner
    |
    | approves architecture, phases, consequential ADRs, releases
    v
Deterministic control plane: agentctl
    |
    +-- artifact validator
    +-- DAG scheduler
    +-- lease and semantic-lock manager
    +-- worktree manager
    +-- context compiler
    +-- verification runner
    +-- review coordinator
    +-- integration queue
    +-- event store and telemetry
    |
    +--> Sol architecture adapter
    +--> Opus planning adapter
    +--> Terra worker/integrator pool
    +--> Sonnet worker/reviewer pool
    +--> Gemini mechanical-worker pool
    |
    v
Isolated Git branches and worktrees
    |
    v
Verified integration branch
~~~

The control plane is not itself an LLM. It may invoke LLMs but does not defer state-transition or permission decisions to them.

## 9. Authority model

### 9.1 Human owner

The human owns:

- product intent;
- architecture approval;
- phase authorization;
- consequential ADR acceptance;
- financial, privacy, and persistence invariant changes;
- destructive or externally visible operations;
- release approval;
- public claims and recruiter-facing evidence.

#### 9.1.1 Human approval artifact

Human authorization is represented by a typed, control-plane-issued record,
never by a chat message, missing objection, or model-authored assertion.

Required fields:

- approval ID and schema version;
- approver principal and local machine identity;
- authentication method and authentication timestamp;
- authorized action;
- target type and target ID;
- exact target hash for the task contract, ADR, phase gate, release, or
  destructive operation;
- bounded scope and conditions;
- reason;
- issued-at and expiry timestamps;
- single-use or reusable-within-scope policy;
- revocation timestamp and reason, when revoked;
- nonce and control-plane signature.

agentctl approve is interactive and obtains a fresh local authentication signal
appropriate to the risk. Ordinary phase authorization may use an interactive
TTY bound to the logged-in OS account. Invariant changes, destructive actions,
and releases require stronger OS authentication through a Keychain-backed or
LocalAuthentication/Authorization Services helper; until that exists, those
transitions remain manually blocked rather than receiving a weaker substitute.

The signing key remains outside model worktrees. Scheduler transitions validate
the signature, action, target hash, scope, expiry, revocation, and consumption
state atomically. A changed target invalidates the approval. A model cannot
create, refresh, broaden, or revoke an approval record.

### 9.2 Deterministic control plane

The control plane alone may:

- validate and register work packages;
- calculate readiness;
- issue and expire leases;
- acquire and release locks;
- create worktrees;
- record attempts;
- invoke configured adapters;
- run required verification;
- accept structured review verdicts;
- determine integration eligibility;
- checkpoint state;
- generate dashboards.

It may not invent product requirements or waive required evidence.

### 9.3 Sol: principal architect

Sol owns:

- global architecture;
- immutable invariant definitions;
- cross-work-package conflict resolution;
- phase-level architecture audits;
- severe ambiguity escalation;
- final recruiter-release invariant audit.

Sol is not a routine implementer or reviewer. A Sol decision that changes product behavior still requires human approval.

### 9.4 Opus: work-package planner

Opus owns:

- decomposition of an approved work package;
- dependency edges;
- interface consumption and production;
- task write sets and semantic-lock proposals;
- acceptance-test coverage;
- integration order;
- evidence-driven replanning.

Opus proposes a DAG. The control plane validates it. Opus does not schedule, lease, merge, or approve its own plan.

### 9.5 Terra: complex implementer and integrator

Terra handles:

- cross-file and cross-module implementation;
- Swift concurrency;
- SwiftData schema and atomic persistence;
- PDFKit and Vision lifecycle work;
- Foundation Models adapter work;
- privacy/security-sensitive code;
- integration conflict resolution;
- escalation from standard workers.

Terra may integrate only candidates with valid gates. It may not use integration authority to change architecture silently.

### 9.6 Sonnet: standard implementer and reviewer

Sonnet handles:

- ordinary domain code;
- UI components;
- adapters behind settled interfaces;
- unit tests;
- documentation;
- routine refactors;
- first-pass review of Gemini work.

Sonnet may not approve its own implementation attempt.

### 9.7 Gemini: mechanical worker

Gemini handles:

- fixture generation from an exact manifest;
- repetitive test matrices;
- repository inventories;
- formatting;
- exact renames when structural authority is not involved;
- generated indexes;
- low-risk documentation transformations.

Gemini does not make architectural, privacy, financial, migration, or security decisions.

### 9.8 Verifier

Verifier is a role, not a fixed model. It:

- runs deterministic gates;
- inspects scope and protected-test integrity;
- emits structured findings;
- never silently edits production code;
- never approves work it authored.

### 9.9 Integrator

The integrator:

- consumes verified candidates in topological order;
- checks base compatibility;
- resolves only mechanical conflicts within approved contracts;
- reruns affected gates after integration;
- requests replanning for semantic conflicts;
- records the integration manifest.

## 10. Separation-of-duty invariants

1. No attempt may be authored and approved by the same model session.
2. A task planner may not mark its plan valid.
3. An implementer may not change scheduler state.
4. A verifier may not repair code while acting as verifier.
5. An integrator may not waive a failed gate.
6. A model-authored ADR remains proposed until the required authority accepts it.
7. Protected tests may not be modified by their subject implementation task.
8. Human approval cannot be inferred from silence.
9. A stronger model does not gain broader file or tool permissions automatically.
10. A model response is an artifact proposal, never authoritative state.

## 11. Control-plane components

### 11.1 agentctl

agentctl is a development-only Python CLI. Python is chosen for portable process control, Git integration, SQLite access, and provider adapters. It is not linked into or shipped with CashBooks.

Initial implementation should prefer:

- Python 3.12 or later;
- standard-library subprocess, sqlite3, json, pathlib, hashlib, and dataclasses;
- JSON machine artifacts;
- Markdown generated views;
- pinned development-only dependencies only when a standard-library implementation would be fragile.

The CLI never processes real user documents. Test PDFs are synthetic repository fixtures.

### 11.2 Artifact validator

The validator checks:

- schema conformance;
- IDs and contract versions;
- dependency references;
- DAG acyclicity;
- required acceptance evidence;
- valid state transitions;
- path normalization;
- prohibited broad paths;
- reviewer independence;
- context and artifact hashes.

### 11.3 Scheduler

The scheduler calculates a ready set from:

- integrated dependencies;
- base compatibility;
- path conflicts;
- semantic locks;
- worker capability;
- reviewer availability;
- global and provider concurrency;
- task and phase budgets;
- human authorization state.

### 11.4 Lease manager

A lease binds:

- lease ID;
- monotonically increasing lease epoch for the task;
- opaque fencing token;
- task ID;
- attempt ID;
- worker identity;
- model and provider;
- base commit;
- worktree;
- locks;
- expiration;
- heartbeat;
- budget.

The lease ID, epoch, and fencing token are included in the dispatch pack and
must be echoed by every candidate, review, and integration artifact. Ingestion
atomically compares them with the currently active lease before accepting an
artifact.

Expired or superseded leases atomically lose their locks and scheduling
authority. Their fencing tokens are invalidated. A late submission is retained
as non-integrable orphan evidence; it cannot advance task state even when its
contract and context hashes remain current. Expiry does not delete the worktree
or attempt record.

### 11.5 Worktree manager

Each attempt receives:

~~~text
branch: agent/<task-id>/<attempt-number>
worktree: generated outside the main checkout
base: exact contract commit
DerivedData: unique per attempt
test results: unique per attempt
~~~

Workers never share a mutable working directory.

### 11.6 Context compiler

The context compiler builds the smallest sufficient context pack and records a content hash. It is described in Section 20.

### 11.7 Verification runner

The runner executes allowlisted checks in a controlled environment and captures:

- command identity;
- exit code;
- normalized output summary;
- full-log artifact path;
- duration;
- environment fingerprint;
- source commit;
- protected-test hashes.

### 11.8 Integration queue

One logical integration queue serializes fan-in. Multiple candidates may wait, but integration happens in dependency order with full affected-gate reruns.

### 11.9 Event store

The runtime event store is local SQLite. Only agentctl writes it.

Recommended tables:

- work_packages;
- tasks;
- dependencies;
- attempts;
- leases;
- semantic_locks;
- artifacts;
- reviews;
- integrations;
- model_runs;
- budgets;
- events.

Every mutation is accompanied by an append-only event record.

The runtime database uses transactional state changes, a single-writer process
lock, SQLite WAL mode, and idempotency keys for retriable commands. Concurrent
agentctl clients submit operations through the same writer boundary rather than
performing independent read-modify-write cycles. Approved plans and completed
artifacts are checkpointed to Git so loss of ephemeral runtime state does not
erase authoritative work.

## 12. Work hierarchy

~~~text
Program
└── Phase
    └── Work package
        └── Task
            └── Attempt
                ├── implementation manifest
                ├── deterministic evidence
                ├── review verdict
                └── integration manifest
~~~

### 12.1 Program

The complete recruiter-first CashBooks effort.

### 12.2 Phase

An authorization and evidence boundary. Phase numbers do not imply that every task inside a phase is serial.

### 12.3 Work package

A cohesive subsystem or milestone slice planned by Opus from Sol-approved architecture.

### 12.4 Task

The smallest independently owned and objectively verifiable unit of implementation.

### 12.5 Attempt

An immutable execution record for a task. Repair or reassignment produces a new attempt number or a new candidate commit under the same bounded repair policy.

## 13. Task-contract schema

Every task contract must include:

### 13.1 Identity

- task ID;
- work-package ID;
- contract version;
- contract hash;
- exact base commit;
- creation and expiry metadata.

### 13.2 Intent

- one objective;
- why the task exists;
- explicit non-goals;
- acceptance behavior;
- relevant architecture decisions.

### 13.3 Dependency contract

- prerequisite task IDs;
- consumed interfaces and versions;
- produced interfaces and versions;
- integration order;
- invalidation triggers.

### 13.4 Scope

- readable paths;
- writable paths;
- prohibited paths;
- semantic locks;
- expected file count or change shape when useful.

### 13.5 Routing

- capability class;
- complexity factors;
- risk class;
- required skills;
- preferred worker;
- minimum reviewer capability;
- required provider diversity, if any.

### 13.6 Permissions

- tool allowlist;
- command allowlist or command classes;
- network policy;
- secret access;
- test-modification policy;
- destructive-operation policy;
- external-side-effect policy.
- required human-approval action and approval-record reference, when applicable.

### 13.7 Verification

- deterministic commands;
- behavioral requirements;
- protected checks;
- manual checkpoints;
- expected artifacts;
- definition of done.

### 13.8 Budgets

- maximum attempts;
- repair cycles;
- maximum artifact-submission corrections;
- wall-clock class;
- token or cost class;
- context-size ceiling;
- tool-call ceiling when appropriate.

### 13.9 Escalation

- local repair conditions;
- capability-escalation conditions;
- replanning conditions;
- architecture-escalation conditions;
- human-approval conditions.

### 13.10 Example

~~~json
{
  "task_id": "CB-M1-PDF-003",
  "work_package_id": "CB-M1-PDF",
  "contract_version": 1,
  "contract_hash": "GENERATED_SHA256",
  "base_commit": "EXACT_COMMIT",
  "intent": {
    "objective": "Extract embedded text from text-backed PDFs using PDFKit",
    "non_goals": [
      "Vision OCR",
      "Foundation Models extraction",
      "UI implementation"
    ]
  },
  "dependencies": {
    "requires": ["CB-M1-CORE-002"],
    "consumes": ["contract:document-pipeline-v1"],
    "produces": ["implementation:pdf-text-extractor"]
  },
  "scope": {
    "readable_paths": [
      "CashBooksCore/**",
      "docs/architecture/**"
    ],
    "writable_paths": [
      "CashBooks/DocumentImport/PDFTextLayerExtractor.swift",
      "CashBooksTests/DocumentImport/PDFTextLayerExtractorTests.swift"
    ],
    "prohibited_paths": [
      "CashBooks.xcodeproj/**",
      "AGENTS.md",
      "CLAUDE.md"
    ],
    "semantic_locks": [
      "implementation:pdf-text-extractor"
    ]
  },
  "routing": {
    "capability_class": "standard_implementation",
    "complexity": "medium",
    "risk": "medium",
    "preferred_worker": "claude-sonnet-5",
    "minimum_reviewer_class": "complex_implementation"
  },
  "permissions": {
    "network": false,
    "destructive_commands": false,
    "may_modify_tests": "task_owned_only"
  },
  "acceptance": {
    "deterministic_checks": [
      "swift build",
      "swift test --filter PDFTextExtractorTests"
    ],
    "behavioral_requirements": [
      "Preserve page numbers",
      "Emit normalized text spans",
      "Reject encrypted PDFs with a typed error",
      "Perform no network requests"
    ],
    "protected_checks": [
      "CashBooksTests/Acceptance/PDFPrivacyBoundaryTests.swift"
    ]
  },
  "budgets": {
    "maximum_attempts": 3,
    "maximum_repair_cycles": 1,
    "maximum_artifact_corrections": 1,
    "token_class": "standard"
  }
}
~~~

## 14. Task state machine

Normal path:

~~~text
draft
  → validated
  → ready
  → leased
  → running
  → candidate
  → deterministic_verification
  → independent_review
  → integration_ready
  → integrating
  → completed
~~~

Exceptional states:

~~~text
blocked
repair_required
replan_required
escalated
failed
canceled
stale
~~~

Rules:

- only the control plane changes state;
- a worker submits a candidate but cannot verify it;
- a verifier submits evidence but cannot merge it;
- an integrator completes only a valid integration;
- a context or contract hash mismatch moves the task to stale;
- a lease expiry marks the current attempt expired, invalidates its fencing
  token, releases its locks atomically, and returns the task aggregate to
  scheduling without erasing evidence;
- canceled and failed attempts remain immutable.

### 14.1 Exceptional transition table

| Current state | Event | Next state |
|---|---|---|
| draft | schema and policy checks pass | validated |
| validated | dependencies, locks, budget, and authorization pass | ready |
| ready | valid lease issued | leased |
| leased | worker acknowledges exact context hash | running |
| running | first recoverable schema-only submission error with valid fencing token | running; artifact correction pending |
| running | repeated schema error or hash/provenance violation | failed attempt; task repair_required or escalated |
| running | typed candidate and commit ingested | candidate |
| candidate | deterministic checks begin | deterministic_verification |
| deterministic_verification | all required checks pass | independent_review |
| deterministic_verification | repairable implementation defect | repair_required |
| independent_review | no blocking findings | integration_ready |
| independent_review | blocking implementation finding | repair_required |
| integration_ready | integration lease issued | integrating |
| integrating | refreshed candidate and affected gates pass | completed |
| any nonterminal | dependency or interface hash changes | stale |
| leased or running | lease expires or is superseded | expired attempt; task ready |
| expired attempt | late artifact arrives | orphan evidence; no state advancement |
| any nonterminal | authorized hard dependency unavailable | blocked |
| repair_required | new attempt authorized within budget | ready |
| repair_required | contract or dependency is invalid | replan_required |
| stale | context rebuilt and contract revalidated | validated |
| blocked | blocker-resolution evidence accepted | validated |
| replan_required | replacement plan accepted | validated or canceled |
| escalated | authority returns a bounded decision | validated, blocked, or canceled |

Repair always creates a distinct attempt record. A completed, failed, or
canceled attempt never returns to an earlier state.

The default ceiling is three execution attempts: initial implementation, one
local evidence-driven repair, and one capability-escalated attempt. A task may
declare a lower ceiling. Raising the ceiling after dispatch requires a recorded
budget decision; an agent cannot extend its own retry allowance.

### 14.2 Invalid submission handling

Artifact schema, path, hash, provenance, and fencing validation runs before any
candidate-controlled command.

- A first recoverable schema-only error under a valid live lease receives
  machine-readable validation errors and one bounded resubmission opportunity.
  It does not create a candidate or consume a new implementation attempt, but
  it consumes the task's artifact-correction budget and does not extend the
  lease.
- A repeated schema error ends the current attempt and routes the task through
  repair policy.
- A fencing, contract-hash, context-hash, commit-provenance, signature, or
  prohibited-path mismatch revokes the lease and triggers a permission or
  integrity audit; it is not treated as a harmless formatting mistake.
- An expired-lease submission becomes orphan evidence.
- Raw invalid output may be retained in redacted local diagnostics, but it never
  becomes a canonical artifact.

## 15. Concurrency admission

A task may run only if:

1. every required dependency is integrated;
2. its contract and context hashes are current;
3. its base commit is compatible;
4. its file write set is disjoint from active tasks;
5. its semantic locks are available;
6. the worker satisfies the capability floor;
7. an eligible independent reviewer can be reserved;
8. provider, machine, and phase budgets permit execution;
9. its execution-enforcement class permits the requested concurrency;
10. a valid, current, hash-bound human-approval record exists when required.

### 15.1 Initial concurrency defaults

Start conservatively and tune from measurements:

- four active implementation workers globally;
- one integration operation;
- one Apple-platform integration-harness lease;
- one live Foundation Models evaluation;
- two concurrent Core test jobs;
- one concurrent full app build until memory measurements justify more;
- provider-specific caps configured separately.

These are operational defaults, not architecture constants.

## 16. Semantic locks

Initial lock vocabulary:

| Lock | Protected resource |
|---|---|
| repo:path-layout | Root migration and directory renames |
| xcode:project-graph | Project file, targets, package links, schemes |
| xcode:platform-matrix | Platforms, deployment targets, actor defaults |
| swiftpm:package-graph | Package manifest, products, targets, resources |
| contract:core-domain-v1 | Money, IDs, dates, shared draft DTOs |
| contract:evidence-lineage-v1 | Field evidence and provenance |
| contract:document-pipeline-v1 | Page, raster, extractor, cancellation APIs |
| contract:draft-extractor-v1 | Rule-based and model interchangeability |
| invariant:risk-monotonicity | Safety merge semantics |
| config:risk-policy-v1 | Signals, weights, caps, thresholds |
| schema:swiftdata-v1 | Records, relationships, migrations |
| contract:confirmation-transaction-v1 | Atomic persistence aggregate |
| sdk:foundation-models-surface | Verified SDK assumptions |
| contract:engine-operation-state-v1 | Headless operation states and transitions |
| contract:review-snapshot-v1 | Immutable draft, evidence, and risk presentation data |
| contract:confirmation-intent-v1 | Deliberate, stale-safe persistence authorization |
| eval:fixture-index-v1 | Fixture identity and labels |
| eval:metrics-schema-v1 | Metric meanings and aggregation |
| eval:protected-oracles | Release-blocking acceptance tests |
| runtime:apple-platform-harness | PDFKit, Vision, SwiftData, and model integration execution |
| policy:agent-constitution-v1 | AGENTS.md and universal authority policy |
| schema:agent-artifacts-v1 | Machine artifact meanings and validation |
| routing:capability-registry-v1 | Capability classes and model mappings |
| docs:generated-indexes | Generated progress, decision, and metric views |

Locks protect semantic ownership, not just files. A consumer pins a lock contract hash. A changed contract invalidates dependent ready or active work.

## 17. Worktree and integration policy

### 17.1 Isolation

- one task attempt per branch and worktree;
- exact base commit in the task contract;
- unique DerivedData and result-bundle paths;
- concurrent tool-using workers run only when an OS, container, or
  provider-native sandbox denies access to other worktrees, the integration
  checkout, control-plane runtime state, and secrets;
- no uncommitted cross-task handoff.

Worktree isolation is a directory layout, not by itself a security boundary.
The scheduler records one of three enforcement classes:

- proposal_only: no filesystem or shell tools; the model returns a patch or
  typed artifact for agentctl to validate and apply;
- sandboxed_tools: tools run with enforced read/write and process boundaries;
- unsandboxed_tools: postcondition checks only, globally serialized, prohibited
  for high-risk tasks.

### 17.2 Candidate submission

A candidate includes:

- current lease ID, epoch, and fencing token;
- result commit;
- changed-file inventory;
- implementation manifest;
- test evidence;
- deviations;
- unresolved risks;
- discovered dependencies.

Candidate ingestion rejects an expired, superseded, mismatched, or already
consumed fencing token before running candidate-controlled commands.

### 17.3 Fan-in

The integration queue:

1. selects the next topologically valid candidate;
2. confirms that evidence and review bind to the exact result commit, tree hash,
   diff hash, contract hash, and verification-evidence hash;
3. checks current dependency and interface hashes;
4. performs only a conflict-free merge that preserves the reviewed candidate
   commit unchanged;
5. reruns all affected gates against the resulting integration tree;
6. records the integration commit and manifest.

A rebase, cherry-pick conflict resolution, manual merge edit, generated-file
change, or any other alteration to the reviewed patch invalidates the review.
The changed result becomes a new candidate with a new manifest, deterministic
verification, and independent review. An integrator may not resolve conflicts
in a candidate it authored.

Workers do not independently resolve cross-task semantic conflicts. Conflicts
that require judgment become bounded integration-repair or replanning tasks.

## 18. Implementation-manifest contract

Required fields:

- task and attempt IDs;
- active lease ID, lease epoch, and fencing token;
- worker role, provider, model, and version;
- task-contract and context hashes;
- base and result commits;
- exact files changed;
- commands executed and actual results;
- acceptance criteria addressed;
- assumptions and decisions;
- deviations from scope;
- newly discovered dependencies;
- unresolved risks;
- token, latency, and cost metadata;
- termination reason.

Worker prose may summarize this manifest but cannot replace it.

## 19. Durable memory model

### 19.1 Constitutional memory

- active product scope;
- architecture;
- security and financial invariants;
- authority policy;
- accepted ADRs.

Changes are rare and strongly reviewed.

### 19.2 Operational memory

- program and work-package DAGs;
- task contracts;
- dependency manifests;
- current interface versions;
- phase authorization.

### 19.3 Episodic memory

- attempts;
- verification evidence;
- review verdicts;
- integration records;
- failures;
- model-run telemetry.

### 19.4 Derived memory

- summaries;
- indexes;
- dashboards;
- context packs;
- routing recommendations.

Derived memory is disposable and must be reproducible from canonical artifacts.

## 20. Context compilation

Each dispatched worker receives:

1. universal authority and safety excerpt;
2. role contract;
3. immutable task contract;
4. relevant accepted ADRs;
5. consumed interface definitions;
6. dependency manifests;
7. applicable verified failure lessons;
8. base commit and repository-state summary;
9. tools and path permissions;
10. budgets, termination rules, and output schema.

It does not receive by default:

- the entire PRD;
- full conversation history;
- unrelated research documents;
- another worker's speculative reasoning;
- secrets;
- imported customer documents;
- obsolete plans;
- unnecessary raw logs.

The compiler generates a manifest and hash. A returned artifact with a different hash is rejected.

## 21. Decision and failure memory

### 21.1 Decisions

Use one ADR per file with:

- ID and title;
- status;
- context;
- considered options;
- decision;
- consequences;
- affected contracts and locks;
- superseded decision, if any;
- required approvals.

Agents may propose ADRs. Only the declared authority accepts them.

### 21.2 Failure lessons

A repeated, verified failure may become a structured lesson:

~~~json
{
  "signature": "swiftdata-cross-actor-model-access",
  "applies_to": [
    "capability:swift-concurrency",
    "component:persistence"
  ],
  "symptoms": [
    "Sendable warning",
    "intermittent ModelContext failure"
  ],
  "verified_resolution": "Pass stable IDs and immutable Core snapshots across actor boundaries",
  "evidence": [
    "CB-M1-PERSIST-008/attempt-3"
  ],
  "confidence": "confirmed"
}
~~~

Only relevant, confirmed lessons enter a context pack. Lessons never override current contracts.

## 22. Verification architecture

The evidence pipeline is:

~~~text
contract validation
→ scope and permission audit
→ build and static checks
→ task-specific tests
→ protected CashBooks invariants
→ independent model review
→ integration suite
→ phase gate
~~~

Evidence priority:

1. reproducible deterministic checks;
2. protected acceptance and invariant tests;
3. repository diff and tool traces;
4. independent reviewer findings;
5. worker explanation and confidence.

Model confidence is never a completion criterion.

## 23. Protected tests and oracle ownership

Protected suites include:

- monetary representation and rounding;
- import and confirmation idempotency;
- risk monotonicity;
- trusted-contact source restrictions;
- evidence-lineage integrity;
- document network isolation;
- model-unavailable fallback;
- atomic persistence;
- cancellation and no-partial-state behavior;
- phase-level end-to-end scenarios.

Subject implementers may add local tests but may not change protected tests. A protected-test change requires:

- a separate task;
- explicit rationale;
- stronger independent review;
- hash update through the control plane;
- human approval when an invariant changes.

The verifier checks for deletion, renaming, skipping, weakened assertions, broadened tolerances, and changed fixture labels.

Critical protected checks are invoked by verifier-owned commands from the
trusted integration base, not by a candidate-modified convenience script. The
verifier also checks test discovery, target membership, scheme configuration,
package manifests, and expected test counts so a worker cannot leave a test
file unchanged while preventing it from running.

Two oracle classes are distinct:

- Public release invariants are committed and readable, but immutable to their
  subject implementation task.
- Hidden routing and reviewer-evaluation cases remain outside dispatched
  worktrees and are used only to calibrate model capability. They are not the
  sole evidence for a product release.

## 24. Independent review

The reviewer initially receives:

- task contract;
- diff;
- deterministic evidence;
- relevant architecture and invariants.

It does not initially receive the author's persuasive summary. After independent analysis, it may compare the implementation manifest to the actual change.

Initial reviewer routing:

| Implementer | Minimum review |
|---|---|
| Gemini | Sonnet |
| Sonnet | Terra |
| Terra | Independent Terra context or Opus specification review |
| Opus plan | Schema validation plus Terra feasibility review |
| Sol architecture | Human approval for consequential changes |

For privacy, financial, persistence, and security work, use a different model family when practical.

Independence is checked using task ID, attempt ID, provider run ID, model
session ID, and actor role. Merely starting a new prompt in the authoring
session does not create an independent reviewer.

### 24.1 Review finding

Each finding includes:

- finding ID;
- severity;
- violated criterion;
- file and location;
- reproducible evidence;
- observed versus required behavior;
- required outcome;
- reviewer confidence;
- blocking status.

The reviewer does not repair code in the same role.

Every review verdict also binds to the reviewed result commit, tree hash, diff
hash relative to its declared base, contract hash, context hash, and
verification-evidence hash. Integration rejects a verdict if any bound value
changes.

## 25. Bounded reflection and failure ladder

Reflection is evidence-driven repair, not an ungrounded request to think harder.

~~~text
failure
→ minimal diagnostic packet
→ root-cause classification
→ one bounded local repair
→ affected gates rerun
~~~

Escalation:

1. Original worker gets one local repair for a valid task.
2. Independent reviewer classifies implementation, contract, dependency, environment, or oracle failure.
3. Implementation failure may route one capability class higher.
4. Invalid assumptions or dependencies return to Opus for one bounded replan.
5. Architecture, privacy, financial, or cross-work-package conflict reaches Sol.
6. Protected-invariant, destructive, schema-commitment, release, or unresolved product decisions require the human.

Retries without new evidence are forbidden.

### 25.1 No-progress detection

Stop or escalate when:

- the same failure signature repeats;
- a repair does not address cited evidence;
- attempts alternate between two broken states;
- previously passing gates regress repeatedly;
- the task or budget expires;
- the worker crosses scope;
- a permission or protected-test violation occurs.

## 26. CashBooks milestone-blocking invariants

### 26.1 Financial

- Money uses fixed-point integer minor units, never Double.
- Currency and rounding policies are explicit.
- Imports, retries, and confirmations are idempotent.
- Duplicate documents cannot silently create duplicate records.
- Persistence of the confirmation aggregate is atomic.
- Locale-sensitive parsing cannot alter stored monetary meaning.

### 26.2 AI authority

- Foundation Models output is an untrusted draft.
- Every material field retains source evidence.
- Deterministic validation precedes persistence.
- Model influence may add but not reduce deterministic risk.
- Rule-based fallback remains functional.
- Model-generated IDs are not authoritative storage IDs.

### 26.3 Document privacy

- PDF ingestion performs no network requests.
- Document bytes and extracted text remain local.
- Security-scoped access is released correctly.
- Temporary data has explicit cleanup.
- Logs and telemetry contain no document text or PII.
- malformed, encrypted, oversized, and unsupported documents fail safely.

### 26.4 Platform correctness

- Core compiles without Foundation Models, PDFKit, Vision, SwiftUI, SwiftData, or networking.
- framework objects remain actor-owned.
- headless engine commands and snapshots crossing actors are immutable and Sendable.
- ModelContext and model records do not cross actors.
- Foundation Models availability is detected.
- automated tests do not require a live model.
- failures and cancellation leave no partial canonical record.

## 27. Model routing

### 27.1 Complexity factors

- architectural uncertainty;
- requirement ambiguity;
- cross-module coupling;
- novelty;
- toolchain difficulty;
- verification difficulty.

### 27.2 Independent risk factors

- financial correctness;
- privacy and security;
- persistence and data loss;
- blast radius;
- reversibility.

Risk may impose a higher reviewer floor even when implementation is simple.

### 27.3 Capability classes

| Class | Initial model | Scope |
|---|---|---|
| architectural_authority | GPT-5.6 Sol | Architecture and phase gates |
| work_package_planning | Claude Opus | DAG and interface planning |
| complex_implementation | GPT-5.6 Terra | Complex code and integration |
| standard_implementation | Claude Sonnet 5 | Routine code and review |
| mechanical_execution | Gemini | Exact, low-risk work |

### 27.4 Hard floors

- Gemini: low risk and objective deterministic acceptance only.
- Sonnet: no sole approval of high-risk financial or privacy changes.
- Terra: no unilateral architecture changes.
- Opus: no permanent manager loop.
- Sol: no routine task execution.

## 28. Routing evaluation

Maintain representative synthetic task packets for:

- money and rounding;
- idempotent transaction behavior;
- PDF text extraction;
- malformed and encrypted PDFs;
- scan detection and OCR geometry;
- evidence lineage;
- risk monotonicity;
- Foundation Models validation and fallback;
- SwiftData isolation and migration;
- privacy boundaries;
- headless operation-state, cancellation, and stale-result behavior.

Evaluate separately:

- planner quality;
- implementation quality;
- reviewer defect detection;
- integration correctness.

Metrics:

- first-pass acceptance;
- success after one repair;
- blocking findings;
- escaped defects;
- protected-test violations;
- scope violations;
- integration conflicts;
- accepted-result latency;
- tokens and monetary cost;
- cost per accepted task;
- human-review minutes;
- failure distribution.

A model version is a new routing candidate. It does not inherit historical trust automatically.

## 29. Champion-challenger policy

For a small, budgeted sample of low- or medium-risk tasks:

~~~text
current champion → eligible merge candidate
cheaper challenger → frozen shadow attempt
deterministic evaluator → comparison
~~~

The challenger is never merged directly. Promotion requires a representative sample, zero critical violations, and acceptable review burden. Critical violations trigger immediate demotion.

Duplicate execution is not routine. It is an evaluation tool.

## 30. Budget policy

Budgets exist at:

- program phase;
- provider;
- model;
- work package;
- task;
- attempt.

Sol is invoked for:

- foundational architecture;
- invariant changes;
- multi-work-package architectural failures;
- phase audits;
- unrecoverable planning conflicts.

Opus is normally limited to one planning pass and one evidence-driven replan per work package.

The control plane records why an expensive model was invoked. Rate limits, context ceilings, attempt limits, and no-progress termination are enforced mechanically.

## 31. Provider-neutral adapter

Every provider adapter implements:

~~~text
run(
  role,
  context_pack,
  task_contract,
  tool_policy,
  budget,
  output_schema
) -> run_result
~~~

Run result includes:

- provider and exact model;
- task, attempt, and context hashes;
- status and termination reason;
- typed output artifacts;
- tool-trace references;
- token, latency, and cost;
- commit or review verdict;
- unresolved errors.

Task contracts refer to capability classes. Model names remain in routing configuration.

## 32. Manual-first operation

Initial workflow:

1. Human authorizes a work package.
2. Opus proposes its DAG.
3. agentctl validates the plan.
4. Scheduler calculates the ready set.
5. agentctl creates worktrees and context packs.
6. Human launches proposal-only sessions or agentctl launches a sandboxed
   tool-using session and supplies the dispatch pack.
7. Workers return commits and manifests.
8. agentctl ingests artifacts and runs deterministic checks.
9. Independent reviewers return verdicts.
10. Terra integrates eligible candidates.
11. The phase gate generates an evidence report.

Planned CLI surface:

~~~text
agentctl status
agentctl plan validate <work-package>
agentctl ready
agentctl dispatch <task-id>
agentctl ingest <task-id> <manifest>
agentctl verify <task-id>
agentctl review prepare <task-id>
agentctl review ingest <task-id> <verdict>
agentctl integrate <task-id>
agentctl gate <phase-id>
agentctl metrics routing
~~~

Manual dispatch and automated provider invocation consume the same contracts.

### 32.1 Manual execution modes

**Proposal-only manual mode**

- The model has no direct filesystem or shell access.
- It returns a typed manifest plus patch or complete artifact.
- agentctl validates paths, hashes, scope, and artifact shape before applying
  the proposal to the assigned worktree.
- Several proposal-only sessions may run concurrently.

**Tool-using manual mode**

- Concurrent execution requires an enforced sandbox scoped to the assigned
  worktree, approved read paths, per-attempt temporary directories, and
  per-attempt build output.
- The sandbox denies other worktrees, the integration checkout, runtime
  database, credentials, and prohibited network access.
- Provider-native workspace restrictions count only after a capability probe
  verifies them.
- If enforcement is unavailable, the scheduler sets the global tool-using
  worker cap to one, rejects high-risk tasks, and denies integration or
  control-plane write paths.

Postcondition diff checks remain mandatory in both modes; they supplement
rather than replace containment.

## 33. Scheduler cycle

~~~text
refresh repository state
→ invalidate stale contexts
→ expire dead leases
→ calculate dependency-ready tasks
→ reject path and semantic conflicts
→ reserve reviewer and integration capacity
→ enforce cost and provider limits
→ issue leases
→ checkpoint state
~~~

All tool side effects should be idempotent where practical. Operations that may be retried require idempotency keys.

## 34. Automation maturity

| Level | Capability |
|---|---|
| 0 | Versioned policies, contracts, and templates |
| 1 | Validation CLI, concurrent proposal-only dispatch, serialized unsandboxed tools |
| 2 | Automated worktrees, leases, verification, integration queue |
| 3 | Codex, Claude, and Gemini adapters |
| 4 | Telemetry-driven routing and shadow evaluation |
| 5 | Bounded automatic replanning and phase reporting |

All artifacts are designed for Level 5. Implementation begins at the smallest reliable level and adds autonomy only after eval evidence.

## 35. Verified repository facts

As of 2026-08-31:

- /Users/himanshu_jain/cashguard is not a Git repository;
- /Users/himanshu_jain/cashguard/AppleBooks contains the existing Git repository;
- the Xcode project still uses the AppleBooks name;
- the project uses filesystem-synchronized root groups;
- supported platforms currently include iOS, macOS, and visionOS;
- app declarations default to MainActor isolation;
- the app source remains essentially the generated SwiftData scaffold.

These facts make repository consolidation, naming, platform policy, actor policy, and package wiring exclusive structural work.

Filesystem-synchronized groups are beneficial after the structural gate: ordinary source and test files can normally be added without every worker editing project.pbxproj.

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

## 37. Program overview

~~~text
P0 Multi-model control plane
  ↓ G0
P1 Repository, naming, and build foundation
  ↓ G1
P2 Contract and oracle freeze
  ↓ G2
┌────────────┬───────────┬────────────┬─────────────┐
Document     Trust/risk  Extraction   Persistence
Engine host  Fixtures    FM adapter   Verification
└────────────┴───────────┴────────────┴─────────────┘
  ↓ headless composition and hardening
G3-B Headless local-intelligence baseline
  └── G3-L Live Foundation Models support claim, when compatible evidence exists
  ↓ new human-approved design required
Deferred UI product program
  ↓
Deferred recruiter-release program
  ↓
Deferred: Plaid, backend, Share Extension, broader accounting
~~~

Phase gates authorize progress. Task dependencies determine concurrency. Only
P0 through G3 are authorized for implementation planning by this
specification.

## 38. P0: Multi-model control plane

### 38.1 Work packages

| ID | Deliverable | Dependencies | Initial capability |
|---|---|---|---|
| P0-A | Root repository consolidation and history preservation | none | complex implementation |
| P0-B | AGENTS.md and thin vendor adapters | P0-A | standard implementation from approved design |
| P0-C | Schemas, examples, roles, routing, event and lease formats | P0-A | complex implementation |
| P0-D | Architecture, threat model, invariant catalog, authority map | P0-A | work-package planning plus complex review |
| P0-E | Historical-document classification and research index | P0-A | mechanical execution plus standard review |
| P0-F | Roadmap, program DAG, and honest recruiter status | P0-B, P0-D | standard implementation |
| P0-I | Cross-linking, validators, examples, and dry-run integration | P0-B through P0-F | complex integration |

P0-B through P0-E may execute concurrently with disjoint write ownership. P0-I owns final cross-links and generated indexes.

### 38.2 G0 control-plane gate

G0 passes only when:

- the root is one Git repository;
- original AppleBooks history remains reachable;
- no nested Git repository remains active;
- example work package, task, implementation, review, integration, and phase-gate artifacts validate;
- a DAG may contain several ready tasks;
- lease and path-lock conflicts are rejected;
- semantic-lock conflicts are rejected;
- self-review is rejected;
- context hashes and stale-task invalidation work;
- document authority is unambiguous;
- documentation checks pass from a clean checkout;
- Sol completes an architecture audit;
- the human approves the active product scope and authority map.

## 39. P1: Structural foundation

### 39.1 Serialized sequence

~~~text
P1-A AppleBooks to CashBooks Xcode migration
→ P1-B platform and concurrency policy freeze
→ P1-C CashBooksCore package and app wiring
→ P1-D root build/test/privacy commands
→ P1-E protected-test ownership and evaluation shell
~~~

No feature work is mixed into these tasks.

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

### 39.4 G1 structural gate

- CashBooks naming is canonical in active code, targets, schemes, and visible product strings.
- Fresh-checkout iOS and macOS builds pass.
- scheme and test discovery pass.
- CashBooksCore builds and tests independently.
- forbidden-import checks prove the Core boundary.
- each worktree can use unique build output.
- protected-test paths are enforceable.
- no feature behavior was added during structural migration.

## 40. P2: Contract and oracle freeze

### 40.1 Foundation tasks

After G1, the following implementations may fan out behind Sol-approved signatures:

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

P2-B and P2-M0 may run while domain contracts are implemented. P2-C and P2-D
may run concurrently after shared primitives integrate. P2-G follows the
domain, risk, and persistence contracts because it composes their immutable
snapshots rather than inventing them. P2-M0 is read-only SDK reconnaissance:
it may inform adapter constraints but may not pull Foundation Models types
into Core.

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

### 40.2 G2 API and oracle freeze

G2 requires:

- all shared contracts compile;
- Money properties pass;
- evidence lineage resolves;
- Core forbidden imports remain absent;
- risk monotonicity has protected tests;
- stored-contact-only verification has protected tests;
- document network isolation has a protected oracle;
- atomic confirmation and idempotency have protected definitions;
- headless operation, cancellation, and stale-result transitions are explicit;
- no-confirmation/no-persistence and stale-confirmation rejection have
  protected tests;
- fixture labels and metric meanings are frozen;
- the installed Foundation Models API surface and mapping constraints are recorded;
- consumers pin interface hashes.

After G2, an interface change is a replanning event, not an incidental worker edit.

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

## 42. Deferred UI product program

The UI program begins only after G3 and requires a new brainstorm, written
design, implementation plan, and human approval. No task in this section is
ready or schedulable under the current specification.

Its scope includes:

- product information architecture and primary user journeys;
- SwiftUI views, navigation, design tokens, animation, and visual polish;
- Files picker and password-entry experience;
- evidence navigation, correction, verification, and confirmation screens;
- trusted-versus-quarantined presentation;
- a MainActor client reducer that consumes engine snapshots;
- VoiceOver, Dynamic Type, contrast, Reduce Motion, and other presentation
  accessibility requirements;
- UI race, accessibility, snapshot, and automation tests; and
- human UX acceptance.

The UI client must depend on CashBooksLocalEngineHost commands and snapshots,
not on Foundation Models, PDFKit, Vision, or SwiftData directly. G4 and its
task IDs are intentionally unassigned until this program is designed.

## 43. Deferred recruiter-release program

A recruiter-ready vertical slice follows the UI product gate. It will combine
the reusable G3 engine evidence with:

- an end-to-end user flow;
- UI accessibility and cancellation evidence;
- a resettable 60–90 second demo;
- representative screenshots or video;
- README claims tied to measured artifacts;
- explicit limitations; and
- human UX and demo acceptance.

G5 and recruiter-release task IDs are intentionally unassigned. The headless
engine may be discussed as verified infrastructure, but not represented as a
finished app.

## 44. Broader deferred program

Only after a later product decision:

- Plaid Sandbox synchronization;
- backend token exchange and sync support, if still justified;
- Mail or Files Share Extension;
- standalone image or camera ingestion;
- expanded cash-flow engines;
- broader accounting workflows;
- App Store/TestFlight operations.

Each deferred feature receives a new product decision and work-package plan. It does not inherit authorization from this design.

## 45. Application runtime concurrency

### 45.1 PDF ownership

PDFDocument and PDFPage are treated as actor-owned, non-Sendable framework objects. Page text extraction and rasterization occur within their owner boundary. Downstream components receive value snapshots.

### 45.2 Vision

OCR concurrency is bounded by a configurable policy. Results include page index and are sorted before document assembly. Cancellation is cooperative and checked before and after render and recognition operations.

### 45.3 Foundation Models

Each import uses an isolated session or request actor. Tool use is disabled for untrusted-document extraction. Generated values map to Core candidates and pass deterministic validation.

### 45.4 SwiftData

~~~text
Sendable engine commands
    ↓
Actor-owned local-engine coordinator
    ↓ immutable commands and snapshots
PersistenceRepository protocol
    ↓
ModelActor SwiftData repository
    ↓
Model records and ModelContext
~~~

Model objects remain inside the repository actor. Callers receive stable IDs and immutable snapshots.

## 46. Agent-system repository layout

~~~text
AGENTS.md
CLAUDE.md
GEMINI.md

docs/
  DOCUMENT_AUTHORITY.md
  product/
    ACTIVE_SCOPE.md
    HERO_SCENARIO.md
    NON_GOALS.md
  architecture/
    SYSTEM.md
    DOCUMENT_PIPELINE.md
    CONCURRENCY.md
    PERSISTENCE.md
    MODEL_AUTHORITY.md
  security/
    THREAT_MODEL.md
    INVARIANTS.md
    PRIVACY_BOUNDARY.md
  decisions/
    README.md
    ADR-*.md
  program/
    ROADMAP.md
    PROGRAM_DAG.md
    PHASE_GATES.md
  archive/
    README.md

agent-system/
  README.md
  VERSION
  schemas/
  policies/
  roles/
  routing/
  templates/
  examples/
  evals/
  work/
  runtime/

tools/
  agentctl/
~~~

### 46.1 Git-tracked artifacts

- active authority documents;
- accepted ADRs;
- protocol schemas;
- policies and roles;
- approved plans and task contracts;
- completed manifests and review verdicts;
- compact gate summaries;
- routing baselines;
- synthetic fixtures.

### 46.2 Git-ignored runtime state

- active leases;
- SQLite runtime database;
- provider session IDs;
- raw transcripts;
- raw build logs;
- temporary worktree metadata;
- secrets;
- token and rate-limit counters;
- temporary document staging.

### 46.3 Required schema set

- work-package.schema.json;
- task-contract.schema.json;
- context-manifest.schema.json;
- implementation-manifest.schema.json;
- review-verdict.schema.json;
- integration-manifest.schema.json;
- human-approval.schema.json;
- phase-gate.schema.json;
- event.schema.json.

## 47. Root instruction-file responsibilities

### 47.1 AGENTS.md

AGENTS.md is the universal constitution. It contains:

- mission and active milestone;
- document authority;
- immutable product and safety invariants;
- role separation;
- task and artifact requirements;
- scope, tool, worktree, and lock rules;
- verification and protected-test rules;
- failure and escalation policy;
- human approval conditions;
- definition of done.

It avoids provider syntax, implementation backlog, and duplicated architecture detail.

### 47.2 CLAUDE.md

CLAUDE.md is a thin adapter:

- read AGENTS.md first;
- Opus plans work packages;
- Sonnet implements or reviews bounded tasks;
- return typed artifacts;
- never treat chat as canonical state;
- never duplicate or weaken universal invariants.

### 47.3 GEMINI.md

GEMINI.md is a thin mechanical-worker adapter:

- exact scope only;
- objective acceptance only;
- no architecture, privacy, financial, migration, or security decisions;
- stop and escalate ambiguity;
- return the required manifest.

### 47.4 Codex

Codex consumes AGENTS.md directly. No CODEX.md duplicate is required.

## 48. Document authority and migration

The intended authority order is:

1. human-approved active scope and invariants;
2. accepted ADRs;
3. active architecture documents;
4. approved program and work-package plans;
5. immutable task contracts;
6. role and provider adapters;
7. historical research and superseded plans.

Migration:

1. preserve the existing root CLAUDE.md as legacy content;
2. replace it with the thin adapter;
3. create AGENTS.md and GEMINI.md;
4. create DOCUMENT_AUTHORITY.md;
5. classify CashGuard-era PRD, TECH_SPEC, TASKS, BOOTSTRAP, INFRA_PLAN, and DESIGN_SPEC as historical/reference unless a current document explicitly incorporates a decision;
6. mark the previous control-plane design as superseded only for orchestration;
7. mark the serial implementation plan as superseded for execution;
8. retain historical research quotations and source names;
9. avoid blind CashGuard-to-CashBooks replacement inside archives.

## 49. Development-control-plane threat model

### 49.1 Assets

- source code and Git history;
- architecture and invariants;
- protected tests and fixture labels;
- credentials for provider adapters;
- developer filesystem and tools;
- integration branch;
- routing and cost budgets;
- model-run telemetry.

### 49.2 Trust boundaries

- human to control plane;
- control plane to model provider;
- model output to artifact ingestion;
- worker worktree to integration branch;
- repository content to model context;
- verification environment to candidate code;
- local runtime state to version-controlled artifacts.

### 49.3 Threats

- prompt injection embedded in repository documents;
- a worker claiming false completion;
- test deletion or weakening;
- path traversal in artifact paths;
- broad or destructive tool use;
- secret exfiltration;
- real document or PII inclusion in prompts;
- poisoned decision or failure memory;
- stale context applied to changed interfaces;
- concurrent edits to semantic invariants;
- malicious or accidental dependency addition;
- provider outage or model-version drift;
- unbounded retries and budget exhaustion;
- integration of a branch based on obsolete code;
- reviewer anchoring or correlated model error.

### 49.4 Mitigations

- repository documents are data unless declared authoritative by the context compiler;
- typed schemas and path normalization;
- least-privilege file and tool policies;
- no secret material in context packs;
- synthetic fixtures only;
- protected-test hashes;
- deterministic verification before model review;
- model and provider diversity for high-risk review;
- contract and context hashes;
- semantic locks;
- exact base commits;
- isolated worktrees;
- bounded attempts and no-progress detection;
- pinned or recorded provider model versions;
- human gates for consequential changes;
- integration reruns after refresh.

### 49.5 Residual risks

- Manual dispatch cannot guarantee tool enforcement as strongly as an automated
  sandbox. Level 1 therefore permits concurrency only for proposal-only or
  positively verified sandboxed sessions. Unsandboxed tool use is serialized,
  secret-free, ineligible for high-risk tasks, and subject to mandatory
  postcondition checks.
- Different model families may still make correlated errors.
- A protected oracle may encode a mistaken requirement.
- A local administrator can bypass repository policy.
- Provider retention and training policies are external controls and must be
  reviewed before enabling an adapter.
- A compromised dependency or compiler can invalidate apparently deterministic
  verification.

These risks are reduced through synthetic data, least privilege, independent
evidence, pinned dependencies, human gates, and honest release documentation;
they are not claimed to be eliminated.

## 50. Product-data boundary for development agents

Cloud coding models may receive:

- source code;
- synthetic fixtures;
- architecture;
- task contracts;
- redacted test output.

They may not receive:

- customer PDFs;
- imported document text;
- real payment instructions;
- account or routing numbers;
- production credentials;
- persisted application databases;
- unredacted model prompts from user documents.

This boundary is independent of the app's runtime local-only guarantee and should be stated explicitly in AGENTS.md.

## 51. Observability and telemetry

Every event includes:

- event and trace IDs;
- task, attempt, work-package, and phase IDs;
- actor role;
- provider and exact model version, when applicable;
- timestamp;
- prior and next state;
- contract and context hashes;
- repository commits;
- artifact references;
- reason code.

Operational metrics:

- ready-queue time;
- execution time;
- verification time;
- review time;
- integration time;
- first-pass yield;
- repair and escalation counts;
- scope and permission violations;
- lock contention;
- stale-context invalidations;
- cost per accepted task;
- human-review time;
- provider failures.

Telemetry must not contain document contents, secrets, hidden model reasoning, or raw PII. Store concise rationales and evidence references, not chains of thought.

## 52. Error taxonomy

| Category | Meaning | Default response |
|---|---|---|
| planning_error | Invalid DAG, incomplete contract, missing gate | Opus repair once, then escalate |
| contract_stale | Dependency, interface, or base changed | Recompile context and revalidate |
| implementation_failure | Valid task, incorrect code | One local repair, then stronger worker |
| verification_failure | Candidate fails objective gate | Return evidence to repair ladder |
| oracle_conflict | Test conflicts with accepted requirement | Separate protected-test review |
| permission_violation | Worker exceeded files/tools/action | Stop attempt and independent audit |
| infrastructure_failure | Toolchain, provider, simulator, disk, or network issue | Retry only after environment evidence |
| invalid_artifact | Provider output fails schema, hash, or provenance validation | Reject without state advancement |
| integration_conflict | Candidate conflicts after fan-in | Mechanical resolve or replan |
| budget_exhausted | Cost, token, attempt, or time limit reached | Stop and escalate |
| no_progress | Repeated failure without meaningful delta | Stop and escalate |
| human_gate | Required authority unavailable | Remain blocked without workaround |

Infrastructure retries do not count as implementation repairs when evidence proves the candidate was not evaluated.

## 53. Recovery and resumability

A fresh agent resumes using only:

- task contract;
- context manifest;
- isolated worktree;
- last valid checkpoint;
- verification evidence;
- unresolved findings.

Recovery procedures:

- expired lease: preserve branch, issue a new attempt after inspection;
- corrupt runtime database: reconstruct from committed artifacts and Git, then restore the last checkpoint;
- provider interruption: retain partial trace, do not infer completion;
- integration failure: leave integration head green and return candidate to repair or replan;
- stale interface: invalidate all affected descendants;
- interrupted platform integration test: release the harness lease after process
  and state verification;
- model-version change: register a new routing candidate and rerun representative evals.

## 54. Generated team ceremonies

Human-readable team views are generated from state:

- sprint planning: approved Opus work-package DAG;
- standup: leases, ready tasks, blockers, and evidence;
- code review: structured verdict;
- incident review: classified failure and corrective action;
- retrospective: phase error analysis and routing changes;
- release review: phase-gate evidence.

Agents do not spend tokens role-playing meetings.

## 55. Trade-offs

### 55.1 More contracts, less improvisation

The system spends more effort defining interfaces and acceptance. This is justified by parallel safety and resumability.

### 55.2 Single integration queue

Fan-in is serialized and can become a bottleneck. For this project size, deterministic integration is more valuable than maximum throughput.

### 55.3 Python development CLI beside a Swift product

This introduces a second implementation language, but the tool is not shipped. Python substantially reduces orchestration cost and keeps app architecture clean.

### 55.4 Model diversity is not independence proof

Cross-provider review reduces anchoring but cannot replace deterministic oracles or human judgment.

### 55.5 Protected tests can also be wrong

They are protected from casual edits, not immutable forever. A separate evidence-backed process may correct them.

### 55.6 Early interface freezes constrain discovery

An interface may need revision. The design permits explicit replanning rather than silent drift.

## 56. What to revisit as the system grows

- raise or lower concurrency based on M4 Pro memory and build measurements;
- replace local SQLite only if several machines must coordinate;
- add remote artifact storage only if local Git artifacts become insufficient;
- add provider APIs after manual adapters demonstrate stable contracts;
- introduce automatic Opus replanning only after planner evals;
- add multiple integration queues only if module isolation makes them safe;
- reconsider visionOS only after a product use case exists;
- add a backend only when a user-facing capability requires it;
- recalibrate all model routing after provider/model version changes.

## 57. Design implementation sequence

After this specification passes review:

1. use the writing-plans workflow to produce the detailed P0 control-plane
   bootstrap plan;
2. add supersession banners to the prior design and serial plan;
3. consolidate the repository root safely;
4. create document authority and preserve legacy content;
5. create AGENTS.md, CLAUDE.md, and GEMINI.md;
6. create schemas, templates, examples, policies, roles, and routing configuration;
7. implement artifact validation and manual agentctl Level 1;
8. encode the program DAG and first work-package contracts;
9. run a documentation-only dry run with several concurrent synthetic tasks;
10. close G0 before touching application source;
11. have Opus produce the exact P1 plan against the integrated G0 commit;
12. close G1, then have Opus produce exact P2 contracts and tasks against the
    real CashBooks project and installed SDK evidence;
13. close G2, then plan and dispatch the independent P3 subsystem work
    packages behind frozen interface hashes;
14. integrate, evaluate, and close the headless G3 gate; and
15. stop. UI work requires a new brainstorm, specification, and human
    authorization.

Detailed P1, P2, and P3 task contracts are intentionally not pre-bound to
fictional base commits, paths, or SDK signatures. Their topology, outcomes,
authority, and gate evidence are fixed here; Opus materializes executable
contracts at the preceding gate using the current repository state.

## 58. Design acceptance criteria

This design is acceptable when:

- deterministic authority is explicit;
- every model role has bounded responsibilities;
- no role can self-approve;
- task state and transitions are unambiguous;
- file and semantic concurrency are both modeled;
- manual and automated dispatch share one protocol;
- durable memory does not depend on chat history;
- review and repair are evidence-driven and bounded;
- cost escalation is controlled;
- application and development privacy boundaries are distinct;
- CashBooks headless-engine lanes and G3 preserve all approved invariants;
- successful atomic persistence and live-model compatibility evidence are
  inside G3 rather than hidden behind UI work;
- full UI and recruiter-release claims are explicitly deferred;
- prior useful work is retained without retaining serial execution authority;
- lower-cost agents can implement the next plan without making architectural decisions.

## 59. Decisions fixed by this specification

1. Deterministic scheduler over LLM-controlled scheduling.
2. Hierarchical planning over free-form delegation.
3. Artifact handoffs over conversational memory.
4. Capability classes over hard-coded provider names in task contracts.
5. Separate complexity and risk classification.
6. Isolated worktrees and exact base commits.
7. File locks plus semantic locks.
8. Deterministic gates before model review.
9. Independent reviewers and protected tests.
10. One bounded repair before escalation.
11. Repository and external state as memory.
12. Python development CLI with JSON artifacts and SQLite runtime state.
13. Manual dispatch first, provider automation later.
14. Early test-oracle and interface freeze.
15. Parallel subsystem implementation after G2.
16. Rule-based offline behavior remains mandatory beside live model integration.
17. PDFKit/Vision and Foundation Models remain outside CashBooksCore.
18. Persistence and the Foundation Models adapter are required G3 work
    packages; UI is a separate deferred program.
19. Synthetic PDFs stay in platform tests; normalized JSON drives Core tests.
20. Sol and Opus remain scarce, boundary-oriented resources.
21. No extractor, model adapter, or risk engine can mint persistence
    confirmation authorization.
22. Exact post-G0 task contracts bind real commits and interface hashes rather
    than speculative planning-time values.

## 60. Glossary

**Artifact:** Structured, versioned output consumed by another role or the control plane.  
**Attempt:** One immutable execution record for a task.  
**Candidate:** A committed implementation awaiting verification or integration.  
**Capability class:** Vendor-neutral task requirement used by routing.  
**Context pack:** Minimal compiled input for one role and task.  
**Contract hash:** Digest of the immutable task contract.  
**Gate:** Machine- and human-verifiable condition for progress.  
**Lease:** Time-bounded ownership of a task and its resources.  
**Oracle:** Trusted expected behavior used to judge a candidate.  
**Protected test:** Oracle that a subject implementer cannot modify.  
**Semantic lock:** Exclusive ownership of a shared concept or invariant.  
**Work package:** Cohesive group of dependent tasks planned together.  
**Worktree:** Isolated Git checkout for one attempt.

## 61. Final architectural statement

CashBooks will use stochastic models as constrained specialists inside a deterministic engineering organization. The repository, contracts, tests, and event history define truth. Models contribute judgment and implementation, but authority remains explicit, evidence-based, and recoverable. The active program ends with a verified headless local-intelligence engine; the user experience is a later, separately designed consumer of that engine.
