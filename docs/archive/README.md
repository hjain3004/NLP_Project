# Archive and historical material

**Status:** Active index. Written at P0 Task 3, 2026-09-01.

Every document that existed in this repository before the P0 control-plane bootstrap is
classified here. Nothing in this directory, and nothing classified below as historical or
reference, authorizes work. The authority order is in
[docs/DOCUMENT_AUTHORITY.md](../DOCUMENT_AUTHORITY.md); everything listed here sits at
level 7.

## Classification

| Document | Classification | Reason |
|---|---|---|
| `docs/archive/CLAUDE-legacy-2026-07.md` | **Superseded** | The CashGuard-era operating manual, written for a single-agent workflow. Replaced by `AGENTS.md` plus thin vendor adapters. Preserved because it records what was expected of a coding agent before the control plane existed. |
| `BOOTSTRAP.md` | **Historical** | CashGuard-era bootstrap sequence. Describes a project start that has since happened differently. |
| `TECH_SPEC.md` | **Reference** | Technical decisions from July 2026. Individual decisions may be re-adopted, but only by explicit incorporation into a current document — never by a worker citing this file. |
| `DESIGN_SPEC.md` | **Reference** | Product invariants from July 2026. Its twelve release-blocking properties informed the current invariant catalogue; the authoritative version is `docs/security/INVARIANTS.md` and `agent-system/policies/invariants.json`. |
| `TASKS.md` | **Historical** | A serial task inventory. Not an execution authority: the active program is a dependency DAG with gates, not a list. |
| `INFRA_PLAN.md` | **Deferred** | Deployment and infrastructure planning. No infrastructure work is authorized before the recruiter-release program, which is itself deferred past G3. |
| `RUNBOOK-tasks-1-2.md` | **Historical** | Migration-era operator instructions used before the control plane existed. Retained as evidence; never an execution authority. |
| `docs/cashguard_prd_v0.2.md` | **Historical / future context** | The full CashGuard PRD. Describes a substantially wider product than the authorized milestone. Useful for understanding intent; not a source of requirements. |
| `docs/superpowers/specs/2026-08-30-cashbooks-control-plane-design.md` | **Superseded for orchestration** | Its product, privacy, document-ingestion, and recruiter-first decisions remain valid. Its single-agent orchestration model is superseded by the 2026-08-31 multi-model design. Carries its own authority banner. |
| `docs/superpowers/plans/2026-08-31-cashbooks-control-plane.md` | **Superseded for execution** | Assumes one ready or active task. Its acceptance criteria and file inventory remain reusable as planning input. Carries its own supersession banner. |
| `docs/superpowers/reviews/2026-09-01-cashbooks-p0-plan-review.md` | **Review evidence only** | Records a historical review of the P0 bootstrap plan. It is evidence about that review, never execution authority or a source of current requirements. |
| `docs/ach.md`, `docs/ebook.md`, `docs/money1.md`, `docs/silo.md`, `docs/22365_19_Agents_v8.md` | **Research corpus** | Extracted text from external whitepapers and reports. Git-ignored. Never a requirement source. |
| `docs/pics/**`, `docs/*.pdf` | **Research corpus** | Source PDFs and page images (~180 MB). Git-ignored. |

## Context-pack policy

Research-corpus documents are **excluded from context packs by default**. The context
compiler does not include them, and `agent-system/policies/product-data-boundary.json`
(created at P0 Task 7) enforces the roots it may read from.

Including one requires an explicit reference in a task contract — a named path in
`readable_paths`, with the task's rationale saying why an external source is needed. That is
a deliberate act recorded in an immutable contract, not a worker's judgement call at
execution time.

This matters for two reasons. The corpus is large enough to crowd out the contract and
interfaces a worker actually needs. And it is external text of unknown provenance: it must
be treated as data, never as instruction, in line with the prompt-injection rule in
`docs/DOCUMENT_AUTHORITY.md`.

## What is not archived

The current authority documents — `AGENTS.md`, `docs/product/**`, `docs/security/**`,
`docs/architecture/**`, `docs/decisions/**`, `docs/program/**`, `agent-system/**` — are
active and live outside this directory. The canonical engineering design and its review
also remain active under `docs/superpowers/`, at level 2b and as review evidence
respectively.
