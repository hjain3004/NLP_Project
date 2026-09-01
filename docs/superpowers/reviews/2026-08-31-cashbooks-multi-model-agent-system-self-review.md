# CashBooks Multi-Model Engineering System — Self-Review

**Date:** 2026-08-31  
**Artifact reviewed:** ../specs/2026-08-31-cashbooks-multi-model-agent-system-design.md  
**Review type:** Requirements, architecture, concurrency, safety, and implementability audit  
**Application source reviewed or changed:** No

## 1. Review method

The review checked:

1. Every approved user requirement from the design conversation.
2. Internal authority and state-machine consistency.
3. Artifact, lease, lock, worktree, and integration semantics.
4. Independent verification and protected-oracle integrity.
5. Apple-platform concurrency and framework boundaries.
6. CashBooks financial, privacy, model-authority, and evidence invariants.
7. Manual-first operation and later provider automation.
8. Expensive-model cost controls and empirical routing.
9. Migration from the older serial control-plane plan.
10. Whether a lower-cost planner can produce an implementation plan without
    reopening architectural decisions.
11. Whether the active program stops at a complete headless engine while all
    SwiftUI product work remains explicitly deferred.

The review also checked Markdown structure, sequential section numbering,
balanced fenced blocks, duplicate headings, unintended trailing whitespace,
and required topic coverage. Two-space Markdown hard breaks in metadata and the
glossary are intentional.

## 2. Requirements coverage

| Requirement | Design evidence | Result |
|---|---|---|
| Deterministic control plane | Sections 1, 8, 9, 11 | Covered |
| Concurrent independent workers | Sections 15–17, 37–41 | Covered |
| Multiple interchangeable workers | Sections 27, 31, 33 | Covered |
| Sol used sparingly | Sections 9.3, 30 | Covered |
| Opus plans bounded work packages | Sections 9.4, 30 | Covered |
| Terra handles complex code/integration | Sections 9.5, 38–41 | Covered |
| Sonnet handles routine code | Sections 9.6, 27 | Covered |
| Gemini handles mechanical work | Sections 9.7, 27 | Covered |
| Vendor-neutral protocol | Sections 13, 18, 31 | Covered |
| Manual first, automated later | Sections 32–34 | Covered |
| Evidence-driven reflection | Sections 22–25 | Covered |
| Persistent external memory | Sections 11.9, 19–21 | Covered |
| Empirical evals and routing | Sections 28–30 | Covered |
| Independent review | Sections 10, 24 | Covered |
| Explicit stop conditions | Sections 14, 25, 52 | Covered |
| PDF local-only ingestion | Sections 26, 36, 41 | Covered |
| PDFKit plus Vision boundary | Sections 36, 41.1, 45 | Covered |
| Foundation Models typed draft only | Sections 26, 41.6 | Covered |
| Headless local-engine program DAG | Sections 37–41 | Covered |
| Successful persistence inside G3 | Sections 40.1, 41.4, 41.8 | Covered |
| Foundation Models adapter and harness inside G3 | Sections 41.6, 41.8 | Covered |
| Explicit confirmation authority | Sections 40.1, 41.4, 41.8 | Covered |
| SwiftUI product work deferred | Sections 5, 37, 42–43 | Covered |
| Preserve earlier valid architecture | Sections 2, 48 | Covered |

## 3. Findings corrected during review

### SR-001 — Important — Exceptional transitions were under-specified

**Evidence:** The first draft listed exceptional states but did not define the
legal path from repair, stale, blocked, replan, or escalation back into normal
execution.

**Correction:** Added Section 14.1 with explicit events and next states.

### SR-002 — Important — SQLite did not yet have a concrete single-writer rule

**Evidence:** The draft said only agentctl writes runtime state but did not
prevent concurrent agentctl processes from racing.

**Correction:** Section 11.9 now requires a process lock, transactions, WAL,
idempotency, and Git checkpoints.

### SR-003 — Important — Foundation Models reconnaissance occurred after G2

**Evidence:** The first DAG placed installed-SDK research after interface
freeze, creating avoidable adapter-feasibility risk.

**Correction:** P2-M0 now runs after G1 and records mapping constraints before
G2. It remains read-only and cannot contaminate Core.

### SR-004 — Important — PDF password lifetime was implicit

**Evidence:** The product design had a zero-retention rule, but the new program
lane did not repeat it.

**Correction:** Section 41.1 now forbids persistence, logging, telemetry, and
model exposure of unlock passwords.

### SR-005 — Important — Manual dispatch enforcement was overstated

**Evidence:** A manually launched provider session may have broader tools than
the Level-1 controller can technically prevent.

**Correction:** Section 49.5 now records this residual risk and requires
secret-free isolated worktrees plus mandatory postcondition checks.

### SR-006 — Important — Invalid provider artifacts lacked an error category

**Evidence:** Schema validation existed, but the error taxonomy did not name the
failure or its state behavior.

**Correction:** Section 52 now rejects invalid artifacts without advancing
state.

### SR-007 — Important — Retry ceiling conflicted with the escalation ladder

**Evidence:** The example allowed two attempts, while the ladder described an
initial attempt, local repair, and stronger-model attempt.

**Correction:** The example and Section 14.1 now define a default ceiling of
three attempts and forbid self-extension.

### SR-008 — Important — Protected tests could be bypassed without file edits

**Evidence:** A worker could theoretically change scheme membership, package
configuration, or a candidate-owned test runner while leaving protected test
files unchanged.

**Correction:** Section 23 now requires verifier-owned commands, discovery and
target checks, expected counts, and manifest/scheme integrity.

### SR-009 — Important — Reviewer independence needed machine identity

**Evidence:** A new prompt in the same model session could be presented as an
independent review.

**Correction:** Section 24 now checks task, attempt, provider-run, session, and
role identities.

### SR-010 — Minor — Control-plane policy files lacked semantic locks

**Evidence:** The first lock table concentrated on application contracts.

**Correction:** Section 16 now locks the agent constitution, artifact schemas,
routing registry, and generated indexes.

### SR-011 — Critical — The previous G3 stopped before persistence and live-model composition

**Evidence:** The deterministic G3 ended at a review-state output while
successful SwiftData confirmation and the live Foundation Models harness were
placed in the former P4 beside UI work.

**Correction:** Sections 37 and 41 now make G3 the complete headless local
engine. It includes deterministic fallback, the typed model adapter and
conditional live harness evidence, deliberate confirmation, atomic
persistence, privacy sentinels, failure injection, and immutable result
snapshots.

### SR-012 — Critical — Confirmation authority was implicit in the deferred UI

**Evidence:** Persistence required owner confirmation, but the only
end-to-end confirmation action was previously described as a screen behavior.
A headless composition could therefore lack a machine-enforced authorization
boundary.

**Correction:** Sections 40.1, 40.2, and 41.4 freeze ConfirmDraftCommand and a
trusted confirmation-authorization boundary. Extractors, model adapters, risk
engines, and repositories cannot mint authorization. Missing, stale,
divergent, and replayed commands have protected behavior.

### SR-013 — Important — UI tasks leaked into the active ready set

**Evidence:** Design tokens, screens, navigation, accessibility presentation,
and UI automation were scheduled before or immediately after the old G3.

**Correction:** Sections 5, 37, and 42–43 remove all UI work from the active
program. The current scope retains only framework-neutral commands, operation
states, and immutable snapshots. A future UI requires a new brainstorm,
specification, plan, and human approval.

### SR-014 — Important — The platform boundary assumed a MainActor UI host

**Evidence:** The previous diagrams and concurrency rules routed persistence
through a MainActor UI store.

**Correction:** Sections 36, 39.3, and 45.4 define an actor-owned headless
local-engine host with Sendable commands and snapshots. SwiftUI isolation is
explicitly a future-program decision.

## 4. Independent-review findings corrected

### IR-001 — Critical — Expired workers lacked lease fencing

**Evidence:** A worker holding an expired lease could still submit a candidate
whose contract and context hashes remained current after a replacement acquired
the locks.

**Correction:** Sections 11.4, 14, 17, and 18 now require lease IDs, monotonic
epochs, opaque fencing tokens, atomic lock invalidation, and non-integrable late
orphan artifacts.

### IR-002 — Critical — Manual concurrency overstated containment

**Evidence:** A worktree directory does not prevent a manually launched
tool-using agent from accessing peer worktrees or shared state.

**Correction:** Sections 15, 17.1, 32.1, 34, and 49.5 now distinguish
proposal-only, sandboxed, and unsandboxed execution. Concurrent tool use
requires verified containment; unsandboxed use is serialized and prohibited
for high-risk, integration, and control-plane work.

### IR-003 — Important — Integration changes could retain stale review

**Evidence:** Conflict resolution or rebase edits could change reviewed code
without requiring another independent verdict.

**Correction:** Sections 17.3 and 24 bind review to exact commit, tree, diff,
contract, context, and evidence hashes. Any changed patch requires a new
candidate, verification, and review.

### IR-004 — Important — Human approval had no typed authority record

**Evidence:** The scheduler required approval but the design did not define
identity, target binding, expiry, revocation, or authentication.

**Correction:** Section 9.1.1 defines signed, hash-bound approval records with
local authentication and atomic scheduler validation. Sections 13.6, 15, and
46.3 carry the requirement into contracts and schemas.

### IR-005 — Important — Invalid artifacts had incomplete recovery semantics

**Evidence:** Rejecting malformed output without state advancement left the
attempt running until lease expiry and did not define budget consumption.

**Correction:** Sections 13.8 and 14.1–14.2 define one bounded schema
resubmission, repeated-error failure, immediate integrity escalation, lease
handling, and attempt-budget behavior.

The independent reviewer re-ran its audit after these corrections. It marked
all five findings resolved and reported no newly introduced Critical or
Important issue.

## 5. No unresolved critical or important findings

The self-review found no contradiction that would permit:

- an LLM to own scheduler state;
- self-approval;
- a model to reduce deterministic risk;
- document text to cross the product network boundary;
- Foundation Models types to enter Core;
- unverified code to enter the integration queue;
- concurrent invention of a shared interface;
- persistence before explicit owner confirmation;
- a headless coordinator or test harness to mint confirmation implicitly;
- active SwiftUI work to enter the G0–G3 scheduler; or
- a G3 completion claim to be represented as a finished recruiter product.

## 6. Open implementation-time validations

These are intentionally deferred evidence tasks, not unresolved architecture:

1. Verify exact installed Foundation Models APIs in P2-M0.
2. Measure safe Xcode/Core build concurrency on the M4 Pro.
3. Confirm provider CLI/API sandbox and structured-output capabilities before
   implementing each adapter.
4. Select pinned JSON-schema validation dependencies, if the standard-library
   validator is insufficient.
5. Validate the exact root-Git migration procedure in the P0 implementation
   plan before moving any repository metadata.
6. Calibrate routing thresholds from real task data rather than inventing
   success percentages now.

## 7. Independent-review status

Two initial independent review turns were interrupted by a model-service usage
limit. Their earlier evidence-gathering had already influenced the design:

- splitting UI from persistence;
- moving PDF fixtures out of Core;
- keeping generated Foundation Models types out of Core;
- defining actor ownership;
- serializing UI tests;
- using semantic locks.

A fresh-context GPT-5.6 Terra reviewer then completed the final audit, returned
the five findings in Section 4, and completed a second pass after correction.
All original findings were verified resolved with no new Critical or Important
issues.

## 8. Structural verification evidence

Fresh local checks confirmed:

- the canonical specification exists;
- it has sequential top-level sections;
- fenced blocks are balanced;
- no duplicate top-level headings exist;
- no unintended trailing whitespace was detected; intentional two-space
  Markdown hard breaks were retained;
- both older orchestration documents contain clear supersession notices;
- required concepts are present: deterministic scheduler, semantic locks,
  protected tests, manual-first operation, P0, headless G3, Python CLI, and
  CashBooksCore boundaries.

## 9. Review verdict

**Verdict: ready for implementation planning.**

The human approved completing the non-UI plan on 2026-08-31. The next
authorized work is the detailed P0 control-plane bootstrap plan and the
gate-bound planning cascade for P1 through G3. Application implementation does
not begin during this planning milestone.
