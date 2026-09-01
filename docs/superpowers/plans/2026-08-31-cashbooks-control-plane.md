# CashBooks Control Plane Implementation Plan

> **SUPERSEDED — DO NOT EXECUTE (2026-08-31):** This plan assumes one ready or
> active task and is not the implementation authority for the approved
> concurrent multi-model workflow. Preserve it as historical planning input:
> its product acceptance criteria, safety requirements, and file inventory may
> be reused. The replacement execution authority is
> [CashBooks P0 Control-Plane Bootstrap Implementation Plan](./2026-09-01-cashbooks-p0-control-plane-bootstrap.md),
> written from
> [CashBooks Multi-Model Engineering System](../specs/2026-08-31-cashbooks-multi-model-agent-system-design.md)
> after that design passed review on 2026-08-31.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a single versioned repository root and a compact, internally consistent control plane that lets lower-cost agents execute the recruiter-first CashBooks milestone without making product, architecture, or security decisions.

**Architecture:** The repository root becomes the single Git boundary. `AGENTS.md` is the canonical execution contract, `CLAUDE.md` is a concise compatibility entry point, and milestone behavior lives in one active specification supported by architecture, threat-model, roadmap, ledger, and bounded-task documents. Existing broad specifications remain historical inputs with explicit authority labels.

**Tech Stack:** Markdown, Git, POSIX shell, `rg`, `make`; existing Swift/Xcode scaffold remains unchanged in this plan.

**Spec:** `docs/superpowers/specs/2026-08-30-cashbooks-control-plane-design.md`

## Global Constraints

- The canonical product name is CashBooks.
- CashBooks is an iOS product; macOS is only a live Foundation Models validation harness.
- The first product milestone uses deterministic synthetic financial fixtures.
- Plaid, a production backend, the Mail/Files Share Extension, standalone image import, and camera scanning are deferred.
- Milestone 1 document bytes and extracted document text never cross a network boundary.
- Deterministic code is the sole authority for money, risk, policy, trusted contacts, and canonical state.
- Model output may add risk but may never clear or reduce a deterministic risk signal.
- Money uses fixed-point integer cents; `Double` is forbidden for monetary values.
- Tests and core functionality must not require Foundation Models availability.
- No new third-party dependency is allowed without a recorded decision and human review.
- This plan changes repository structure and documentation only; it does not implement application features or rename the Xcode project.

---

## File and responsibility map

### Files created by this plan

```text
.gitignore                              Repository hygiene and local research exclusions
AGENTS.md                               Canonical cross-agent operating contract
README.md                               Honest recruiter-facing landing page
Makefile                                Root documentation-verification entry point
scripts/verify_control_plane.sh         Deterministic control-plane consistency checks

docs/specs/recruiter-vertical-slice.md  Active milestone behavior and boundaries
docs/research/README.md                  Non-authoritative research index
docs/ARCHITECTURE.md                     Module, data-flow, and trust boundaries
docs/THREAT_MODEL.md                     Assets, adversaries, threats, mitigations, residual risk
docs/ROADMAP.md                          Ordered milestone gates
docs/DECISIONS.md                        Compact accepted architecture decisions
docs/PROGRESS.md                         Execution evidence and handoffs
docs/REVIEW_QUEUE.md                     Batched human review items
docs/BLOCKERS.md                         Reproductions after exhausted attempts
docs/METRICS.md                          Measurement definitions and current baseline state
docs/tasks/README.md                     Task contract schema and state-transition rules
docs/tasks/CB-001-naming-migration.md
docs/tasks/CB-002-core-package-shell.md
docs/tasks/CB-003-money-and-evidence-types.md
docs/tasks/CB-004-document-artifact-types.md
docs/tasks/CB-005-payment-fingerprint-engine.md
docs/tasks/CB-006-risk-policy-engine.md
docs/tasks/CB-007-pdf-import-validation.md
docs/tasks/CB-008-pdfkit-text-extraction.md
docs/tasks/CB-009-vision-ocr-fallback.md
docs/tasks/CB-010-rule-based-draft-extraction.md
docs/tasks/CB-011-foundation-models-adapter.md
docs/tasks/CB-012-review-confirmation-ui.md
docs/tasks/CB-013-adversarial-evaluation.md
docs/tasks/CB-014-performance-accessibility-demo.md
```

### Files modified by this plan

```text
CLAUDE.md                              Replaced by concise AGENTS.md entry point
BOOTSTRAP.md                           Receives historical-status banner
DESIGN_SPEC.md                         Receives authority/status banner
INFRA_PLAN.md                          Receives deferred-scope banner
TASKS.md                               Receives historical-plan banner
TECH_SPEC.md                           Receives authority/status and naming banner
docs/cashguard_prd_v0.2.md             Receives historical-product banner
```

### Files intentionally not modified

```text
AppleBooks/**/*.swift                  Xcode rename is CB-001, after this plan
AppleBooks/**/*.pbxproj                Xcode rename is CB-001, after this plan
docs/*.pdf                             Local research sources, ignored by root Git
docs/pics/**                           Generated research extraction, ignored by root Git
docs/{22365_19_Agents_v8,ach,ebook,money1,silo}.md
                                       Indexed as research; content remains unchanged
```

---

### Task 1: Consolidate the repository under one Git root

**Files:**
- Create: `.gitignore`
- Preserve: `AppleBooks/.git` history as subtree history under the new root
- Preserve locally: `AppleBooks/AppleBooks.xcodeproj/xcuserdata/`
- Do not modify: application source or project configuration

**Interfaces:**
- Consumes: the nested `AppleBooks` repository at commit `441a6f8` and the unversioned root documentation tree.
- Produces: one Git repository rooted at `/Users/himanshu_jain/cashguard`, with no nested `.git` directory and with the original Xcode commit reachable as a parent of the import merge commit.

- [ ] **Step 1: Record and verify the pre-migration state**

Run:

```bash
pwd
git -C AppleBooks status --short
git -C AppleBooks rev-parse HEAD
git -C AppleBooks log -1 --oneline
test ! -e .git
```

Expected:

```text
/Users/himanshu_jain/cashguard
?? AppleBooks.xcodeproj/xcuserdata/
441a6f877409c6abf55a8afdb616b9dd51c528e6
441a6f8 Initial Commit
```

If tracked files are modified, stop and preserve them before migrating. The currently known untracked `xcuserdata` directory is user-specific and will remain untracked.

- [ ] **Step 2: Create and verify a recoverable history bundle**

Run:

```bash
test ! -e /private/tmp/cashbooks-git-migration-20260831
generated_backup_dir="$(mktemp -d /private/tmp/cashbooks-git-migration.XXXXXX)"
mv "$generated_backup_dir" /private/tmp/cashbooks-git-migration-20260831
git -C AppleBooks bundle create /private/tmp/cashbooks-git-migration-20260831/applebooks.bundle --all
git bundle verify /private/tmp/cashbooks-git-migration-20260831/applebooks.bundle
```

Expected: bundle verification reports the `main` branch and a complete history. The recoverable backup location is `/private/tmp/cashbooks-git-migration-20260831`.

- [ ] **Step 3: Move the existing nested working tree to the recoverable backup location**

Run:

```bash
mv AppleBooks /private/tmp/cashbooks-git-migration-20260831/AppleBooks-working-tree
test ! -e AppleBooks
test -d /private/tmp/cashbooks-git-migration-20260831/AppleBooks-working-tree/.git
```

Expected: all commands succeed. This is a recoverable move, not a deletion.

- [ ] **Step 4: Initialize the canonical root repository**

Run:

```bash
git init -b main
git rev-parse --show-toplevel
```

Expected root:

```text
/Users/himanshu_jain/cashguard
```

- [ ] **Step 5: Add repository hygiene rules**

Create `.gitignore` with exactly:

```gitignore
.DS_Store
**/.DS_Store

# Xcode user and build state
**/xcuserdata/
*.xcuserstate
DerivedData/
*.moved-aside

# Swift build output
.build/

# Secrets
.env
.env.*
!.env.example

# Local research source files and generated extraction artifacts
docs/*.pdf
docs/pics/
```

- [ ] **Step 6: Commit the root documentation without large local research artifacts**

Run:

```bash
git add .gitignore BOOTSTRAP.md CLAUDE.md DESIGN_SPEC.md INFRA_PLAN.md TASKS.md TECH_SPEC.md docs
git status --short
git ls-files 'docs/*.pdf' 'docs/pics/**'
git commit -m "chore: establish CashBooks repository root"
```

Expected:

- Markdown specifications and the approved design/plan are staged.
- `git ls-files` prints nothing for PDFs and `docs/pics`.
- The commit succeeds.

- [ ] **Step 7: Import the original Xcode commit under the existing directory prefix**

Run:

```bash
git remote add applebooks-history /private/tmp/cashbooks-git-migration-20260831/applebooks.bundle
git fetch applebooks-history main
git merge -s ours --no-commit --allow-unrelated-histories applebooks-history/main
git read-tree --prefix=AppleBooks/ -u applebooks-history/main
git commit -m "chore: import AppleBooks Xcode history"
git remote remove applebooks-history
git log --all --oneline --decorate --graph -8
```

Expected: root history includes the original `441a6f8 Initial Commit` as the second parent of `chore: import AppleBooks Xcode history`. The `AppleBooks/` working tree is restored without `AppleBooks/.git`.

- [ ] **Step 8: Restore ignored user-specific Xcode state and compare working trees**

Run:

```bash
if [ -d /private/tmp/cashbooks-git-migration-20260831/AppleBooks-working-tree/AppleBooks.xcodeproj/xcuserdata ]; then
  cp -R /private/tmp/cashbooks-git-migration-20260831/AppleBooks-working-tree/AppleBooks.xcodeproj/xcuserdata AppleBooks/AppleBooks.xcodeproj/
fi
diff -qr -x .git -x xcuserdata /private/tmp/cashbooks-git-migration-20260831/AppleBooks-working-tree AppleBooks
test ! -d AppleBooks/.git
git status --short
```

Expected:

- `diff` prints nothing.
- no nested `.git` exists;
- restored `xcuserdata` is ignored;
- root working tree is clean.

- [ ] **Step 9: Record migration evidence in the eventual progress ledger**

Do not create a separate commit yet. Retain these facts for Task 5:

```text
Repository migration: complete
Original nested commit: 441a6f8 Initial Commit
Migration verification: subtree history reachable; source tree diff empty
Recoverable bundle: /private/tmp/cashbooks-git-migration-20260831/applebooks.bundle
```

### Task 2: Establish the canonical agent operating contract

**Files:**
- Create: `AGENTS.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: approved control-plane design and its authority rules.
- Produces: one canonical cross-agent contract plus a concise Claude compatibility entry point.

- [ ] **Step 1: Run the precondition check and observe failure**

Run:

```bash
test -f AGENTS.md
```

Expected: non-zero exit because `AGENTS.md` does not exist.

- [ ] **Step 2: Create `AGENTS.md` with the complete operating contract**

Use these exact top-level headings, in this order:

```markdown
# AGENTS.md — CashBooks Engineering Contract
## 1. Mission and current milestone
## 2. Read order and document authority
## 3. Immutable product and safety invariants
## 4. Architecture boundaries
## 5. Task selection
## 6. Per-task execution loop
## 7. Testing and verification rules
## 8. Dependencies, privacy, and logging
## 9. Failure and blocker protocol
## 10. Human escalation conditions
## 11. Repository hygiene
## 12. Definition of done
```

The mission must state that CashBooks is an iOS-first, independent pre-payment trust layer and that the active milestone is `docs/specs/recruiter-vertical-slice.md`.

The authority section must list:

```text
1. AGENTS.md
2. docs/specs/recruiter-vertical-slice.md
3. TECH_SPEC.md where compatible
4. DESIGN_SPEC.md
5. docs/ROADMAP.md
6. docs/cashguard_prd_v0.2.md as historical/future context
7. research as non-authoritative
```

The invariant section must state all twelve release-blocking properties from design-spec section 12, including this exact semantic rule:

```text
Model output may add risk evidence, but no model-derived value may clear, reduce, override, or suppress a deterministic risk signal.
```

The task loop must require:

```text
read assigned task -> inspect existing state -> write failing test/check -> implement smallest change -> run exact gate -> record evidence -> self-review -> hand off and stop
```

The escalation section must limit mandatory human stops to security/privacy/money contradictions, new external data transmission, credentials, Apple signing/entitlements, irreversible user-data changes, or destructive repository operations not already authorized by a task.

- [ ] **Step 3: Replace `CLAUDE.md` with the compatibility entry point**

`CLAUDE.md` must be no more than 120 lines and contain:

```markdown
# CLAUDE.md — CashBooks Entry Point

Before doing anything, read `AGENTS.md` in full. It is the canonical engineering contract. Then read only the assigned task in `docs/tasks/` and the sections it references.
```

It must also contain:

- the active milestone link;
- the seven-item authority order;
- the intentionally repeated critical safety subset from design-spec section 8;
- a direction to stop after the assigned task and leave a handoff; and
- no independent work loop, fallback matrix, or contradictory failure policy.

- [ ] **Step 4: Verify contract completeness and non-duplication**

Run:

```bash
test -f AGENTS.md
test "$(wc -l < CLAUDE.md | tr -d ' ')" -le 120
rg -q '^# AGENTS.md — CashBooks Engineering Contract$' AGENTS.md
rg -q 'docs/specs/recruiter-vertical-slice.md' AGENTS.md CLAUDE.md
rg -q 'Model output may add risk evidence' AGENTS.md
rg -q 'Before doing anything, read `AGENTS.md` in full' CLAUDE.md
! rg -q 'cashguard_prd_v0_2.md' AGENTS.md CLAUDE.md
```

Expected: all commands succeed.

- [ ] **Step 5: Commit the operating contract**

Run:

```bash
git add AGENTS.md CLAUDE.md
git commit -m "docs: define CashBooks agent operating contract"
```

### Task 3: Establish active milestone authority and classify historical material

**Files:**
- Create: `docs/specs/recruiter-vertical-slice.md`
- Create: `docs/research/README.md`
- Modify: `BOOTSTRAP.md`
- Modify: `DESIGN_SPEC.md`
- Modify: `INFRA_PLAN.md`
- Modify: `TASKS.md`
- Modify: `TECH_SPEC.md`
- Modify: `docs/cashguard_prd_v0.2.md`

**Interfaces:**
- Consumes: control-plane design sections 1-16.
- Produces: one concise active product/technical milestone specification and unambiguous status banners on older material.

- [ ] **Step 1: Demonstrate that the active milestone file is absent**

Run:

```bash
test -f docs/specs/recruiter-vertical-slice.md
```

Expected: non-zero exit.

- [ ] **Step 2: Create the active milestone specification**

Create `docs/specs/recruiter-vertical-slice.md` with these headings:

```markdown
# CashBooks Recruiter-First Vertical Slice
## Status and authority
## Product promise
## Target user and hero scenario
## Scope
## Explicit exclusions
## Platform boundaries
## Document-ingestion flow
## Domain contracts
## Model-authority boundary
## Privacy and retention
## Failure behavior
## Acceptance tests
## Exit gate
```

It must define the product promise as an independent, on-device pre-payment trust layer that works alongside accounting software. It must include the approved hero scenario: a known vendor supplies changed payment details and an untrusted replacement phone number; the app retains the risk and uses the previously verified contact.

Define these planned contract names without implementation bodies:

```swift
struct Money
struct DocumentArtifact
struct ExtractedPage
struct EvidenceReference
struct UntrustedDocument
struct InvoiceDraft
struct PaymentRequestDraft
struct PaymentFingerprint
struct RiskSignal
struct RiskDecision

protocol DocumentTextExtractor
protocol DraftExtractor
protocol PaymentFingerprintEvaluating
protocol RiskEvaluating
```

The specification must say these are interface commitments for subsequent plans, not code created by this control-plane plan.

- [ ] **Step 3: Create the research index**

Create `docs/research/README.md` with:

- a statement that research never overrides active specifications;
- one-sentence relevance and limitation notes for each of:
  - `docs/22365_19_Agents_v8.md`;
  - `docs/ach.md`;
  - `docs/ebook.md`;
  - `docs/money1.md`;
  - `docs/silo.md`;
  - `docs/pics/final_document.md`;
- a note that local source PDFs and generated `docs/pics` artifacts are intentionally excluded from Git to keep the recruiter repository small; and
- a statement that Plaid-sponsored material is useful context but not independent product validation.

- [ ] **Step 4: Add explicit status banners to older specifications**

Insert immediately after each title:

For `TECH_SPEC.md`:

```markdown
> **Authority note:** This remains the lower-level implementation reference where it agrees with `AGENTS.md` and `docs/specs/recruiter-vertical-slice.md`. “CashGuard” names in this historical document refer to the product now named CashBooks. The recruiter-first milestone overrides its broader sequencing.
```

For `DESIGN_SPEC.md`:

```markdown
> **Authority note:** This remains the visual and interaction reference where it agrees with the active CashBooks milestone. Historical CashGuard naming is retained until the dedicated naming migration.
```

For `BOOTSTRAP.md`, `TASKS.md`, and `INFRA_PLAN.md`:

```markdown
> **Status:** Historical planning input. Active execution is governed by `AGENTS.md`, `docs/specs/recruiter-vertical-slice.md`, and `docs/ROADMAP.md`.
```

For `docs/cashguard_prd_v0.2.md`:

```markdown
> **Status:** Historical product and future-scope reference. The canonical product name is CashBooks, and the recruiter-first milestone is defined in `docs/specs/recruiter-vertical-slice.md`.
```

- [ ] **Step 5: Verify the authority cleanup**

Run:

```bash
test -f docs/specs/recruiter-vertical-slice.md
test -f docs/research/README.md
rg -q '^# CashBooks Recruiter-First Vertical Slice$' docs/specs/recruiter-vertical-slice.md
rg -q 'independent, on-device pre-payment trust layer' docs/specs/recruiter-vertical-slice.md
rg -q 'previously verified contact' docs/specs/recruiter-vertical-slice.md
rg -q 'Historical planning input' BOOTSTRAP.md TASKS.md INFRA_PLAN.md
rg -q 'lower-level implementation reference' TECH_SPEC.md
rg -q 'Historical product and future-scope reference' docs/cashguard_prd_v0.2.md
! rg -q 'cashguard_prd_v0_2.md' AGENTS.md CLAUDE.md docs/specs/recruiter-vertical-slice.md
```

Expected: all commands succeed.

- [ ] **Step 6: Commit active-scope documentation**

Run:

```bash
git add BOOTSTRAP.md DESIGN_SPEC.md INFRA_PLAN.md TASKS.md TECH_SPEC.md docs/cashguard_prd_v0.2.md docs/specs/recruiter-vertical-slice.md docs/research/README.md
git commit -m "docs: establish recruiter-first milestone authority"
```

### Task 4: Document architecture and threat boundaries

**Files:**
- Create: `docs/ARCHITECTURE.md`
- Create: `docs/THREAT_MODEL.md`

**Interfaces:**
- Consumes: active milestone scope and immutable invariants.
- Produces: component contracts and security boundaries used by every feature plan.

- [ ] **Step 1: Demonstrate that architecture artifacts are absent**

Run:

```bash
test -f docs/ARCHITECTURE.md
test -f docs/THREAT_MODEL.md
```

Expected: the first missing-file check exits non-zero.

- [ ] **Step 2: Create `docs/ARCHITECTURE.md`**

Use these headings:

```markdown
# CashBooks Architecture
## System context
## Milestone-1 component map
## Module dependency rules
## Document-ingestion sequence
## Trust boundaries
## Persistence boundary
## Model adapter boundary
## Error and cancellation semantics
## Deferred integrations
## Architecture conformance checks
```

Include both diagrams from design-spec sections 10 and 11. State explicitly:

- `CashBooksCore` cannot import Foundation Models, PDFKit, Vision, SwiftUI, SwiftData, or networking frameworks;
- platform adapters convert framework values into core DTOs;
- document acquisition, text extraction, semantic extraction, validation, risk evaluation, confirmation, and persistence are separate stages;
- the backend does not exist in milestone 1; and
- macOS is only a validation harness.

- [ ] **Step 3: Create `docs/THREAT_MODEL.md`**

Use these headings:

```markdown
# CashBooks Milestone-1 Threat Model
## Scope and security objective
## Assets
## Trust boundaries
## Adversaries
## Entry points
## Abuse cases and mitigations
## Security invariants
## Verification strategy
## Residual risks
## Out-of-scope risks
```

The abuse-case table must include:

| Threat | Required mitigation |
|---|---|
| Prompt injection in PDF text | Tool-less extraction, typed output, deterministic validation |
| Changed payment instructions | Stored fingerprint comparison; mismatch cannot be model-cleared |
| Fake phone/email in document | Verification UI uses only previously stored trusted contact |
| Malformed or oversized PDF | Validate before parsing; typed failure; no partial record |
| OCR ambiguity | Incomplete/conflicting draft requiring user resolution |
| Duplicate import | Content hash warning before canonicalization |
| Sensitive data in logs | Privacy-aware logging and log-capture tests |
| Accidental document upload | No document network API; network-boundary verification |
| Model unavailability/refusal | Deterministic fallback preserving safety outcome |
| Cancellation mid-import | Delete temporary artifacts; atomic canonical persistence |

Residual risks must honestly include OCR/model extraction errors, owner override mistakes, compromised previously stored contacts, local-device compromise, lack of vendor bank-account ownership verification, and absence of cross-customer intelligence.

- [ ] **Step 4: Verify architecture/threat coverage**

Run:

```bash
rg -q 'CashBooksCore.*cannot import' docs/ARCHITECTURE.md
rg -q 'backend does not exist' docs/ARCHITECTURE.md
rg -q 'Prompt injection in PDF text' docs/THREAT_MODEL.md
rg -q 'Fake phone/email in document' docs/THREAT_MODEL.md
rg -q 'Residual risks' docs/THREAT_MODEL.md
rg -q 'cannot be model-cleared' docs/THREAT_MODEL.md
```

Expected: all commands succeed.

- [ ] **Step 5: Commit architecture and threat model**

Run:

```bash
git add docs/ARCHITECTURE.md docs/THREAT_MODEL.md
git commit -m "docs: define CashBooks architecture and threat model"
```

### Task 5: Add the roadmap and persistent execution ledgers

**Files:**
- Create: `docs/ROADMAP.md`
- Create: `docs/DECISIONS.md`
- Create: `docs/PROGRESS.md`
- Create: `docs/REVIEW_QUEUE.md`
- Create: `docs/BLOCKERS.md`
- Create: `docs/METRICS.md`

**Interfaces:**
- Consumes: approved sequencing and repository migration evidence.
- Produces: persistent state that survives short agent sessions.

- [ ] **Step 1: Demonstrate that the ledgers are absent**

Run:

```bash
for path in docs/ROADMAP.md docs/DECISIONS.md docs/PROGRESS.md docs/REVIEW_QUEUE.md docs/BLOCKERS.md docs/METRICS.md; do test -f "$path"; done
```

Expected: non-zero exit at the first missing file.

- [ ] **Step 2: Create `docs/ROADMAP.md`**

Use this milestone order:

```text
M0 Control plane and single Git root
M1 CashBooks naming migration
M2 CashBooksCore package and verification shell
M3 Deterministic money, evidence, fingerprint, and risk domain
M4 Local PDF acquisition, validation, text extraction, and Vision fallback
M5 Rule-based and Foundation Models typed draft extraction
M6 Review, provenance, confirmation, and local persistence UI
M7 Adversarial evaluation, performance, accessibility, and hero demo
M8 Plaid Sandbox synchronization
M9 Mail/Files Share Extension
```

M0 is complete only when `make docs-check` passes. M1 is the sole next ready milestone. M8 and M9 may not start before M7 passes.

- [ ] **Step 3: Create `docs/DECISIONS.md` with accepted decisions**

Record these entries, each with Context, Decision, Consequences, and Date:

```text
ADR-001 Canonical name is CashBooks
ADR-002 Recruiter-first vertical slice precedes full PRD
ADR-003 Synthetic fixtures precede Plaid
ADR-004 In-app Files importer precedes Share Extension
ADR-005 iOS is product; macOS is model-validation harness
ADR-006 Root directory is the single Git repository
ADR-007 Milestone-1 documents never cross a network boundary
ADR-008 SystemLanguageModel on-device path only; no PCC or cloud LLM
ADR-009 AGENTS.md is canonical; CLAUDE.md is a compatibility entry point
```

- [ ] **Step 4: Create the remaining ledgers with honest initial state**

`docs/PROGRESS.md` must record:

- design approval date;
- implementation-plan path;
- repository migration evidence retained from Task 1;
- control-plane tasks completed so far with commit hashes; and
- a next-action pointer to the current task.

`docs/REVIEW_QUEUE.md` begins with the severity definitions `fyi`, `should-review`, and `must-review`, followed by `No open review items.`

`docs/BLOCKERS.md` defines the blocker-entry schema and states `No active blockers.`

`docs/METRICS.md` defines:

- field exact-match accuracy;
- OCR failure rate;
- validation-rejection rate;
- fallback rate;
- risk precision/recall and false-positive count;
- safety downgrade count;
- p50/p95 latency; and
- peak memory.

It must state that measurements do not yet exist because application implementation has not begun; values will come from named hardware and committed fixtures rather than estimates.

- [ ] **Step 5: Verify ledger completeness**

Run:

```bash
rg -q 'M1 CashBooks naming migration' docs/ROADMAP.md
rg -q 'M8 Plaid Sandbox synchronization' docs/ROADMAP.md
for adr in ADR-001 ADR-002 ADR-003 ADR-004 ADR-005 ADR-006 ADR-007 ADR-008 ADR-009; do rg -q "$adr" docs/DECISIONS.md; done
rg -q 'No open review items' docs/REVIEW_QUEUE.md
rg -q 'No active blockers' docs/BLOCKERS.md
rg -q 'safety downgrade count' docs/METRICS.md
rg -q 'measurements do not yet exist' docs/METRICS.md
```

Expected: all commands succeed.

- [ ] **Step 6: Commit roadmap and ledgers**

Run:

```bash
git add docs/ROADMAP.md docs/DECISIONS.md docs/PROGRESS.md docs/REVIEW_QUEUE.md docs/BLOCKERS.md docs/METRICS.md
git commit -m "docs: add CashBooks roadmap and execution ledgers"
```

### Task 6: Create the bounded lower-cost-agent task queue

**Files:**
- Create: `docs/tasks/README.md`
- Create: `docs/tasks/CB-001-naming-migration.md`
- Create: `docs/tasks/CB-002-core-package-shell.md`
- Create: `docs/tasks/CB-003-money-and-evidence-types.md`
- Create: `docs/tasks/CB-004-document-artifact-types.md`
- Create: `docs/tasks/CB-005-payment-fingerprint-engine.md`
- Create: `docs/tasks/CB-006-risk-policy-engine.md`
- Create: `docs/tasks/CB-007-pdf-import-validation.md`
- Create: `docs/tasks/CB-008-pdfkit-text-extraction.md`
- Create: `docs/tasks/CB-009-vision-ocr-fallback.md`
- Create: `docs/tasks/CB-010-rule-based-draft-extraction.md`
- Create: `docs/tasks/CB-011-foundation-models-adapter.md`
- Create: `docs/tasks/CB-012-review-confirmation-ui.md`
- Create: `docs/tasks/CB-013-adversarial-evaluation.md`
- Create: `docs/tasks/CB-014-performance-accessibility-demo.md`

**Interfaces:**
- Consumes: roadmap, architecture, active spec, and required task schema.
- Produces: one ready task and thirteen dependency-ordered planned tasks, each executable without an architecture decision.

- [ ] **Step 1: Create the task schema**

`docs/tasks/README.md` must require these exact fields:

```markdown
# CB-### — Task title
**Status:** planned | ready | in_progress | blocked | done
**Milestone:** M#
**Depends on:** task IDs or `none`
## Objective
## Why this exists
## Required reading
## Allowed files
## Inputs
## Outputs and interfaces
## Invariants
## Decided implementation notes
## Tests/checks to add
## Verification commands
## Manual checkpoint
## Explicit non-goals
## Definition of done
## Handoff record
```

State transitions must be:

```text
planned -> ready: prerequisites verified and human/roadmap authorizes task
ready -> in_progress: assigned agent begins
in_progress -> done: every gate passes and evidence is recorded
in_progress -> blocked: three distinct failed approaches or an authorized hard stop
blocked -> ready: blocker is resolved and a fresh task run begins
```

Only one task may be `ready` or `in_progress` at a time during the recruiter-first milestone.

- [ ] **Step 2: Create CB-001 as the sole ready task**

`CB-001-naming-migration.md` must specify:

- rename `AppleBooks` project, targets, schemes, source/test directories, module symbols, and visible strings to CashBooks;
- preserve both iOS and macOS buildability;
- use a dedicated plan before editing `project.pbxproj`;
- modify only naming-related files;
- verify scheme discovery, iOS build, macOS build, and test discovery;
- avoid feature implementation, package creation, or domain-model changes; and
- record rollback instructions before structural edits.

Its status is `ready`, milestone is M1, and dependency is `M0 control-plane gate`.

- [ ] **Step 3: Create CB-002 through CB-014 as planned tasks**

Use this dependency chain and deliverable:

| Task | Depends on | Independently reviewable deliverable |
|---|---|---|
| CB-002 | CB-001 | `CashBooksCore` Swift package shell, root verification commands, empty app dependency |
| CB-003 | CB-002 | Fixed-point `Money`, evidence identifiers/references, injected clock conventions |
| CB-004 | CB-003 | Document artifact, extracted-page, untrusted-document, draft, and provenance DTOs |
| CB-005 | CB-004 | TOFU-inspired payment fingerprint construction/comparison with unit tests |
| CB-006 | CB-005 | Deterministic additive risk engine and monotone merge tests |
| CB-007 | CB-004 | Security-scoped PDF acquisition, type/header/size/page/encryption/hash validation |
| CB-008 | CB-007 | PDFKit embedded-text extraction with explicit per-page quality policy |
| CB-009 | CB-008 | Vision OCR fallback for failed pages, cancellation, and extraction metadata |
| CB-010 | CB-004, CB-006, CB-009 | Rule-based draft extraction and deterministic validation path |
| CB-011 | CB-010 | Tool-less Foundation Models guided generation, validation, availability routing, fallback |
| CB-012 | CB-011 | Quarantined evidence UI, trusted-contact verification, confirmation, atomic persistence |
| CB-013 | CB-012 | Labeled PDF/adversarial corpus, metamorphic tests, extraction/risk metrics |
| CB-014 | CB-013 | Instruments measurements, accessibility checks, resettable hero demo, recruiter README update |

Each task must include exact planned source/test file paths, the interfaces it consumes and produces, tests with named behaviors, verification commands, and explicit exclusions. If an exact API signature requires the installed SDK, the task must instruct the agent to inspect the SDK first, record the verified signature in `docs/DECISIONS.md`, and remain within the already-decided interface boundary.

Use these exact source/test ownership paths so task authors do not invent repository structure:

| Task | Allowed implementation paths |
|---|---|
| CB-002 | `CashBooksCore/Package.swift`, `CashBooksCore/Sources/CashBooksCore/CashBooksCore.swift`, `CashBooksCore/Tests/CashBooksCoreTests/PackageSmokeTests.swift`, `CashBooks/CashBooks.xcodeproj/project.pbxproj` |
| CB-003 | `CashBooksCore/Sources/CashBooksCore/Domain/Money.swift`, `CashBooksCore/Sources/CashBooksCore/Domain/Evidence.swift`, `CashBooksCore/Sources/CashBooksCore/Support/DateProviding.swift`, `CashBooksCore/Tests/CashBooksCoreTests/Domain/MoneyTests.swift`, `CashBooksCore/Tests/CashBooksCoreTests/Domain/EvidenceTests.swift`, `CashBooksCore/Tests/CashBooksCoreTests/Domain/DateProvidingTests.swift` |
| CB-004 | `CashBooksCore/Sources/CashBooksCore/Documents/DocumentArtifact.swift`, `CashBooksCore/Sources/CashBooksCore/Documents/ExtractedPage.swift`, `CashBooksCore/Sources/CashBooksCore/Documents/UntrustedDocument.swift`, `CashBooksCore/Sources/CashBooksCore/Domain/InvoiceDraft.swift`, `CashBooksCore/Sources/CashBooksCore/Domain/PaymentRequestDraft.swift`, `CashBooksCore/Tests/CashBooksCoreTests/Documents/DocumentArtifactTests.swift`, `CashBooksCore/Tests/CashBooksCoreTests/Documents/ExtractedPageTests.swift`, `CashBooksCore/Tests/CashBooksCoreTests/Documents/UntrustedDocumentTests.swift`, `CashBooksCore/Tests/CashBooksCoreTests/Domain/DraftProvenanceTests.swift` |
| CB-005 | `CashBooksCore/Sources/CashBooksCore/Risk/PaymentFingerprint.swift`, `PaymentFingerprintEvaluator.swift`, `CashBooksCore/Tests/CashBooksCoreTests/Risk/PaymentFingerprintEvaluatorTests.swift` |
| CB-006 | `CashBooksCore/Sources/CashBooksCore/Risk/RiskSignal.swift`, `CashBooksCore/Sources/CashBooksCore/Risk/RiskDecision.swift`, `CashBooksCore/Sources/CashBooksCore/Risk/RiskEngine.swift`, `CashBooksCore/Tests/CashBooksCoreTests/Risk/RiskEngineTests.swift`, `CashBooksCore/Tests/CashBooksCoreTests/Risk/RiskMetamorphicTests.swift` |
| CB-007 | `CashBooks/CashBooks/DocumentImport/PDFImportService.swift`, `CashBooks/CashBooks/DocumentImport/PDFImportError.swift`, `CashBooks/CashBooks/DocumentImport/DocumentArtifactFactory.swift`, `CashBooks/CashBooksTests/DocumentImport/PDFImportValidationTests.swift`, `CashBooks/CashBooksTests/Fixtures/PDF/valid-text-one-page.pdf`, `CashBooks/CashBooksTests/Fixtures/PDF/locked-one-page.pdf`, `CashBooks/CashBooksTests/Fixtures/PDF/malformed-truncated.pdf` |
| CB-008 | `CashBooks/CashBooks/DocumentImport/PDFTextLayerExtractor.swift`, `CashBooks/CashBooks/DocumentImport/PageTextQualityPolicy.swift`, `CashBooks/CashBooksTests/DocumentImport/PDFTextLayerExtractorTests.swift`, `CashBooks/CashBooksTests/DocumentImport/PageTextQualityPolicyTests.swift`, `CashBooks/CashBooksTests/Fixtures/PDF/digitally-generated-invoice.pdf`, `CashBooks/CashBooksTests/Fixtures/PDF/empty-text-layer.pdf` |
| CB-009 | `CashBooks/CashBooks/DocumentImport/VisionOCRPageExtractor.swift`, `CashBooks/CashBooks/DocumentImport/HybridDocumentTextExtractor.swift`, `CashBooks/CashBooksTests/DocumentImport/VisionOCRPageExtractorTests.swift`, `CashBooks/CashBooksTests/DocumentImport/HybridDocumentTextExtractorTests.swift`, `CashBooks/CashBooksTests/Fixtures/PDF/scanned-invoice.pdf`, `CashBooks/CashBooksTests/Fixtures/PDF/mixed-pages.pdf`, `CashBooks/CashBooksTests/Fixtures/PDF/rotated-scan.pdf` |
| CB-010 | `CashBooksCore/Sources/CashBooksCore/Extraction/DraftExtractor.swift`, `CashBooksCore/Sources/CashBooksCore/Extraction/RuleBasedDraftExtractor.swift`, `CashBooksCore/Sources/CashBooksCore/Extraction/DraftValidator.swift`, `CashBooksCore/Tests/CashBooksCoreTests/Extraction/RuleBasedDraftExtractorTests.swift`, `CashBooksCore/Tests/CashBooksCoreTests/Extraction/DraftValidatorTests.swift` |
| CB-011 | `CashBooks/CashBooks/AgentFM/FoundationModelsDraftExtractor.swift`, `CashBooks/CashBooks/AgentFM/AgentProvider.swift`, `CashBooks/CashBooksTests/AgentFM/AgentProviderTests.swift`, manual model-evaluation documentation in `docs/METRICS.md` |
| CB-012 | `CashBooks/CashBooks/ImportFlow/DocumentImportStore.swift`, `CashBooks/CashBooks/ImportFlow/DocumentImportView.swift`, `CashBooks/CashBooks/ImportFlow/DraftReviewView.swift`, `CashBooks/CashBooks/ImportFlow/UntrustedEvidenceView.swift`, `CashBooks/CashBooks/ImportFlow/VerificationView.swift`, `CashBooks/CashBooks/Persistence/ConfirmedRecordStore.swift`, `CashBooks/CashBooksTests/ImportFlow/DocumentImportStoreTests.swift`, `CashBooks/CashBooksTests/Persistence/ConfirmedRecordStoreTests.swift`, `CashBooks/CashBooksUITests/DocumentImportFlowUITests.swift` |
| CB-013 | `CashBooksCore/Tests/Fixtures/Documents/normal-known-vendor.pdf`, `CashBooksCore/Tests/Fixtures/Documents/known-vendor-bank-change.pdf`, `CashBooksCore/Tests/Fixtures/Documents/known-vendor-prompt-injection.pdf`, `CashBooksCore/Tests/Fixtures/Documents/duplicate-invoice.pdf`, `CashBooksCore/Tests/Fixtures/Documents/conflicting-totals.pdf`, `CashBooksCore/Tests/Fixtures/corpus.json`, `CashBooksCore/Tests/CashBooksCoreTests/Evaluation/ExtractionEvaluationTests.swift`, `CashBooksCore/Tests/CashBooksCoreTests/Evaluation/RiskEvaluationTests.swift`, `CashBooksCore/Tests/CashBooksCoreTests/Evaluation/SafetyMetamorphicTests.swift`, `docs/METRICS.md` |
| CB-014 | `CashBooks/CashBooksUITests/HeroFlowUITests.swift`, `docs/demo-runbook.md`, `docs/METRICS.md`, `README.md`; source changes are limited to accessibility identifiers and defects exposed by measured gates |

- [ ] **Step 4: Verify task status and dependency integrity**

Run:

```bash
test "$(find docs/tasks -maxdepth 1 -name 'CB-*.md' | wc -l | tr -d ' ')" -eq 14
test "$(rg -l '^\*\*Status:\*\* ready$' docs/tasks/CB-*.md | wc -l | tr -d ' ')" -eq 1
test "$(rg -l '^\*\*Status:\*\* planned$' docs/tasks/CB-*.md | wc -l | tr -d ' ')" -eq 13
rg -q '^\*\*Status:\*\* ready$' docs/tasks/CB-001-naming-migration.md
for file in docs/tasks/CB-*.md; do
  rg -q '^## Objective$' "$file"
  rg -q '^## Allowed files$' "$file"
  rg -q '^## Invariants$' "$file"
  rg -q '^## Verification commands$' "$file"
  rg -q '^## Explicit non-goals$' "$file"
  rg -q '^## Definition of done$' "$file"
done
```

Expected: all commands succeed.

- [ ] **Step 5: Commit the bounded task queue**

Run:

```bash
git add docs/tasks
git commit -m "docs: add bounded CashBooks implementation task queue"
```

### Task 7: Create the honest recruiter-facing landing page

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: active scope, architecture, threat model, and roadmap.
- Produces: a truthful project overview that distinguishes implemented groundwork from planned application behavior.

- [ ] **Step 1: Demonstrate that the root README is absent**

Run:

```bash
test -f README.md
```

Expected: non-zero exit.

- [ ] **Step 2: Create `README.md`**

Use these headings:

```markdown
# CashBooks
## Current status
## Product thesis
## Why this is technically different
## Recruiter-first hero scenario
## Architecture
## Safety properties
## What is implemented today
## Milestones
## Verification
## Documentation map
## Honest limitations
```

The opening must describe CashBooks as:

```text
An iOS-first, on-device pre-payment trust layer for owner-operated businesses.
```

The current-status section must say that the control plane and specifications are complete while application implementation remains at the Xcode scaffold until CB-001 begins. It must not claim that PDF ingestion, Foundation Models, risk engines, or Plaid already work.

The technical-difference section must explain:

- probabilistic inference versus deterministic authority;
- local PDFKit/Vision processing;
- evidence lineage;
- monotone model influence;
- rule-based fallback; and
- adversarial/metamorphic evaluation.

The limitations must state that CashBooks does not replace accounting software, move money, verify bank-account ownership, guarantee fraud detection, or keep future Plaid traffic on-device.

- [ ] **Step 3: Verify recruiter-facing honesty**

Run:

```bash
rg -q '^# CashBooks$' README.md
rg -q 'on-device pre-payment trust layer' README.md
rg -q 'application implementation remains at the Xcode scaffold' README.md
rg -q 'does not replace accounting software' README.md
! rg -qi 'production-grade|guarantees fraud|replaces QuickBooks|fully implemented' README.md
```

Expected: all commands succeed.

- [ ] **Step 4: Commit the recruiter landing page**

Run:

```bash
git add README.md
git commit -m "docs: add honest CashBooks project overview"
```

### Task 8: Add deterministic control-plane verification and close M0

**Files:**
- Create: `scripts/verify_control_plane.sh`
- Create: `Makefile`
- Modify: `docs/PROGRESS.md`
- Modify: `docs/ROADMAP.md`

**Interfaces:**
- Consumes: every artifact produced in Tasks 1-7.
- Produces: `make docs-check`, a single green M0 gate, and an explicit handoff to CB-001.

- [ ] **Step 1: Write the failing root command**

Create `Makefile` with:

```make
.PHONY: docs-check

docs-check:
	@bash scripts/verify_control_plane.sh
```

Run:

```bash
make docs-check
```

Expected: failure because `scripts/verify_control_plane.sh` does not exist.

- [ ] **Step 2: Create `scripts/verify_control_plane.sh`**

Create the executable script with this logic:

```bash
#!/usr/bin/env bash
set -euo pipefail

required_files=(
  AGENTS.md
  CLAUDE.md
  README.md
  docs/specs/recruiter-vertical-slice.md
  docs/ARCHITECTURE.md
  docs/THREAT_MODEL.md
  docs/ROADMAP.md
  docs/DECISIONS.md
  docs/PROGRESS.md
  docs/REVIEW_QUEUE.md
  docs/BLOCKERS.md
  docs/METRICS.md
  docs/tasks/README.md
)

for required_file in "${required_files[@]}"; do
  if [[ ! -f "$required_file" ]]; then
    printf 'missing required control-plane file: %s\n' "$required_file" >&2
    exit 1
  fi
done

task_count="$(find docs/tasks -maxdepth 1 -name 'CB-*.md' | wc -l | tr -d ' ')"
ready_count="$( (rg -l '^\*\*Status:\*\* ready$' docs/tasks/CB-*.md || true) | wc -l | tr -d ' ')"
active_count="$( (rg -l '^\*\*Status:\*\* in_progress$' docs/tasks/CB-*.md || true) | wc -l | tr -d ' ')"

[[ "$task_count" -eq 14 ]] || { printf 'expected 14 task contracts, found %s\n' "$task_count" >&2; exit 1; }
[[ "$ready_count" -eq 1 ]] || { printf 'expected exactly 1 ready task, found %s\n' "$ready_count" >&2; exit 1; }
[[ "$active_count" -eq 0 ]] || { printf 'expected no task in progress at M0 close, found %s\n' "$active_count" >&2; exit 1; }

rg -q '^# AGENTS.md — CashBooks Engineering Contract$' AGENTS.md
rg -q '^# CLAUDE.md — CashBooks Entry Point$' CLAUDE.md
rg -q '^# CashBooks$' README.md
rg -q '^# CashBooks Recruiter-First Vertical Slice$' docs/specs/recruiter-vertical-slice.md
rg -q 'Model output may add risk evidence' AGENTS.md
rg -q 'document bytes and extracted document text never cross a network boundary' AGENTS.md

if [[ -d AppleBooks/.git ]]; then
  printf 'nested AppleBooks/.git still exists\n' >&2
  exit 1
fi

if [[ -n "$(git ls-files 'docs/*.pdf' 'docs/pics/**')" ]]; then
  printf 'large local research artifacts are tracked\n' >&2
  exit 1
fi

if rg -n 'T[B]D|T[O]DO|PLACEHOLDER' AGENTS.md CLAUDE.md README.md docs/specs/recruiter-vertical-slice.md docs/ARCHITECTURE.md docs/THREAT_MODEL.md docs/ROADMAP.md docs/tasks; then
  printf 'unfinished marker found in active control-plane documentation\n' >&2
  exit 1
fi

printf 'CashBooks control-plane verification passed\n'
```

Run:

```bash
chmod +x scripts/verify_control_plane.sh
make docs-check
```

Expected:

```text
CashBooks control-plane verification passed
```

- [ ] **Step 3: Record the final gate and close M0**

Append to `docs/PROGRESS.md`:

```text
M0 gate: make docs-check
Result: PASS — CashBooks control-plane verification passed
Next ready task: CB-001 naming migration
```

Mark M0 complete in `docs/ROADMAP.md`. Do not change CB-001 from `ready`.

- [ ] **Step 4: Run the complete final consistency check**

Run:

```bash
make docs-check
git status --short
git log --oneline --decorate -10
git log --all --oneline | rg '441a6f8|Initial Commit'
```

Expected:

- documentation check passes;
- only Task 8 files are modified before the commit;
- recent history shows one focused commit per task;
- the original Xcode initial commit remains reachable.

- [ ] **Step 5: Commit and verify a clean handoff**

Run:

```bash
git add Makefile scripts/verify_control_plane.sh docs/PROGRESS.md docs/ROADMAP.md
git commit -m "chore: verify CashBooks control plane"
make docs-check
git status --short
```

Expected:

```text
CashBooks control-plane verification passed
```

`git status --short` prints nothing except intentionally ignored local artifacts.

---

## M0 handoff criteria

Do not begin CB-001 until all are true:

- root Git history includes the original Xcode initial commit;
- no nested `.git` exists;
- large local research artifacts remain present locally but untracked;
- `AGENTS.md` is canonical and `CLAUDE.md` contains no independent competing workflow;
- active, historical, and research documents have unambiguous authority;
- architecture and threat boundaries match the approved design;
- exactly one bounded task is ready;
- the README makes no unimplemented product claim;
- `make docs-check` passes from the repository root; and
- the working tree is clean.

After M0, write a separate implementation plan for CB-001 before changing the Xcode project. Do not combine the naming migration with CashBooksCore scaffolding or feature work.
