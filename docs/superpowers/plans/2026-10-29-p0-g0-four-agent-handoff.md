# P0 and G0 Four-Agent Completion Handoff

> **For agentic workers:** Use the four-seat execution method explicitly chosen by the
> owner. Run independently contracted coding lanes in parallel only after their consumed
> interfaces are fixed. The owner's execution method takes precedence over a skill's
> default serial subagent workflow. This handoff is a planning proposal, not a lease,
> approved task contract, human approval record, or G0 completion artifact.

**Goal:** Deliver working development-model routing and integration, resolve the actual
remaining P0 findings, and close G0 through its existing approval and evidence mechanisms.

**Architecture:** Reuse agentctl's scheduler, context compiler, leases, event store,
candidate verifier, and review manager. Two implementation lanes feed one independent
verification and integration controller. Planning is a separate seat and cannot approve
its own plan. Refresh the exact base and all bindings before each dispatched task.

**Tech Stack:** Python standard library, Git, SQLite, provider CLIs, Markdown and JSON.
**Spec:** `docs/program/AUTOMATION_DELIVERY.md`, current AGENTS.md, and the applicable
sections of `docs/superpowers/specs/2026-08-31-cashbooks-multi-model-agent-system-design.md`.
**Baseline inspected:** `5442761372e8fa1b4681705062ba83c98adaa941` on main, 2026-10-29.
**Date discipline:** October 22's delivery target is historical and overdue. Do not copy
it into a new contract as an unexpired deadline or invent a new guaranteed finish date.

## Global Constraints

- Four concurrent seats total. Additional tools, providers, or fresh review sessions replace
  a seat; they do not silently add a fifth worker. Do not spawn background workers.
- No application, Swift, Xcode, UI, release, or public-claim work. Stop after G0 and notify
  the owner before application development; Swift MCP setup must be verified first.
- The coordinator plans and delegates; coding belongs to the execution seats. Do not
  convert the planning seat into a routine implementer when a worker reports a problem.
- Separate worktree and branch per implementation attempt, exhaustive task scope, real
  identity records, contract/context hashes, and valid lease bindings as required by AGENTS.
  These prompts provide startup instructions; they do not themselves satisfy that protocol.
- Initial discovery is read-only. Planning/governance proposals do not advance execution
  state. Use the established bootstrap path where it demonstrably applies; do not invent
  retrospective leases, approvals, manifests, or claims of control-plane execution.
- Reuse passing evidence for unchanged code. Every new check must protect a named behavior
  or failure. Real Git, process, and event-store behavior matter; mock success and test
  totals do not prove that the requested automation works.
- Preserve the current dirty documentation edits and unrelated untracked `output/`.
  No broad staging, cleanup, history rewriting, or push. A controller may propose a
  reviewed documentation-only commit rather than discarding those edits.
- Model bindings belong only in `agent-system/routing/capability-registry.json`. Verify
  actual executable model identifiers and record what ran. Kiro and Antigravity are not
  sufficient model identities. Do not silently substitute a provider or weaken a role.
- Typed approvals remain required where AGENTS specifies them. Prepare exact proposals,
  hashes and supported approval commands; only the human supplies the authentication.
  Continue unaffected tasks while an individual approval is pending.
- Worker sessions do not share mutable PROGRESS.md or METRICS.md files. Publish unique
  attempt artifacts; only the controller maintains the consolidated status.

## Seats and ownership

| Seat | Owner's tool/model choice | Responsibility |
|---|---|---|
| 1 | Opus | One bounded plan; task contracts, frozen interfaces, approval proposals |
| 2 | Sol | Independent plan feasibility review; scoped verification and controlled integration |
| 3 | Sonnet | Complex integration queue and assigned security/governance repairs |
| 4 | Luna | Routing/provider execution behind fixed interfaces and assigned mechanical repairs |

Kiro or Antigravity can replace Seat 4 for an exactly specified low-risk task. Record the
actual provider/model underneath. They may not independently take over security decisions,
shared schema edits, approval issuance, or final acceptance.

## Startup and sequencing

1. Start Seats 1 and 2 in fresh chats. Seat 1 proposes the bounded completion DAG. Seat 2
   inspects actual state and reviews that proposal independently; it does not author and
   approve the same plan. Do not launch another general architecture redesign.
2. Seats 3 and 4 may do the discovery explicitly scoped by their prompts immediately.
   Coding begins only after the controller supplies the exact validated contract, context,
   lease, base commit, and worktree. If absent, return a concise READY_FOR_CONTRACT handoff
   instead of polling or repeatedly rereading the repository.
3. Fix any genuinely necessary shared interfaces in one serial task before the split.
   Sonnet owns changes to shared scheduler/review/store internals unless the plan assigns
   another single owner. Luna consumes that interface and must not independently edit it.
4. Run integration-queue development and routing/provider development in parallel when
   contracts and semantic locks permit. Review completed candidates while other work runs.
   Do not hold every review until both lanes finish.
5. Governance repairs can proceed when their approvals and locks allow. Sequence overlapping
   `gate.py`, `approve.py`, schema, and policy work explicitly. If a reviewed candidate's
   bindings become stale, use the prescribed refresh/replan path; do not rebase it silently.
6. Demonstrate the complete live workflow and two concurrent, disjoint meaningful tasks.
   Measure actual overlap, integrated outcomes, elapsed time, manual intervention and
   provider-reported usage. Unavailable usage must not be fabricated as zero.
7. Recycle the finished planning seat into a fresh architecture-audit session that authored
   none of the audited artifacts. The original planner session cannot audit its own plan.
   Ingest the valid audit, obtain final human approvals, and evaluate G0 on the final tree.
8. Commit the required closure evidence and rerun the prescribed post-commit checks. The
   phase is complete only when its recorded state, manifests, approvals and final Git tree
   agree. P1 is a separate subsequent task, not an automatic continuation of this handoff.

## Review Focus

1. Provider authentication/model access, timeout, nonzero exit, malformed output and
   cancellation: return an actionable failure without a success transition or silent retry.
2. Session/run identity, context, lease and fencing: stale or mismatched submissions must
   never become current candidates; output cannot fabricate its own trusted authority.
3. Candidate/review/tree drift and concurrent requests: accept only current independently
   reviewed bindings, serialize fan-in, and prevent duplicate integration.
4. Conflict, failed affected check, or interruption between Git and database updates:
   preserve the integration branch on failure and reconcile actual state before retry.
5. Approval/scope drift and missing historical evidence: bind exact targets, preserve honest
   gaps, and distinguish actual mandatory blockers from newly invented requirements.

The plan author must assign each focus to the owning task's behavioral checks. The final
reviewer evaluates the implementation; this handoff does not pre-approve any design.

## Existing findings to classify once

The current ledger is
`agent-system/runtime/sdd/p0-finalize-20261018/progress.md`. The original temporary audit
packets may no longer exist. Treat its findings as leads, not as replacement audit evidence.
Reproduce only the relevant claim and cite the authoritative requirement and current code.

- Integrator role text assigns queue selection to the model instead of the deterministic queue.
- Required routing/manifest fields are absent from some schemas/templates. The existing
  `agent-system/work/P0/proposals/governance-change-set-v2.json` may cover these; verify its
  current before-hashes and scope before proposing another change.
- Consequential ADR acceptance needs an enforced, approved path; ADR-0005's actual status
  and decision binding must be inspected, not assumed accepted from implementation alone.
- Approval output containment has a `/var` versus `/private/var` alias regression. A custom
  TMPDIR workaround is not evidence that the default command works.
- G0-03's criteria/evaluator behavior differs; G0-05 through G0-09 use reason-string presence
  where the audit requested committed semantic replay and evidence binding.
- HERO_SCENARIO's claimed authority and the approved scope set disagree. Prepare the exact
  include-versus-reclassify choice for the owner; do not silently choose or discard content.
- Historical session/run attribution is incomplete. Verify precisely what the actual
  independence rule requires. Never invent identities or infer that rewriting history is
  necessary. If a rule cannot be satisfied, present a bounded decision with its exact impact.
- Archive classification, invalid-fixture taxonomy and AI-07 source metadata were also
  flagged. Distinguish mandatory correctness from nonblocking cleanup and avoid expanding scope.
- Provider usage may not fit the current required numeric manifest fields. Inspect the
  selected provider's real output first. If a schema amendment is necessary, prepare the
  smallest exact approved change; do not invent numeric values for missing information.

## Task handoffs and evidence

Seat 1 proposes a work package and per-task immutable contracts in the existing schema
shapes, plus ownership, dependencies, exact reads/writes/commands, semantic locks, reviewer
floor, budgets and acceptance coverage. Proposed artifacts are not executable leases.
Seat 2 validates through the existing control plane and returns exact approved execution
packets through the established handoff mechanism. If a required mechanism is missing,
identify the specific bootstrap dependency and use the documented route; never invent
approval or scheduler authority to make the prompt appear executable.

Use `P0-CLOSEOUT-20261029` as a proposed package identifier, subject to existing validators.
Each task reports its actual commit and unique artifact references. Put retained final
evidence in tracked artifact locations according to existing schemas, not solely `/tmp`.

## Acceptance and stopping point

The requested automation is delivered only after real automatic selection, invocation,
result collection, candidate verification, independent review and unchanged integration
work together. Two concurrent tasks establish parallel execution for that demonstrated
case only; they do not establish a 4x speedup or a whole-project completion estimate.

G0 is closed only after its final criteria pass with valid, exact-target human approvals,
an independent architecture audit, the required commit/replay checks and an authoritative
recorded transition. Report automation delivery and G0 closure separately if either remains
blocked. No worker may claim both from a green infrastructure suite.

## Ready-to-paste startup prompts

- `docs/program/prompts/p0-g0-2026-10-29/01-opus-planner.md`
- `docs/program/prompts/p0-g0-2026-10-29/02-sol-controller-reviewer.md`
- `docs/program/prompts/p0-g0-2026-10-29/03-sonnet-integration.md`
- `docs/program/prompts/p0-g0-2026-10-29/04-luna-routing.md`

The owner has already selected the execution method. This handoff does not request another
generic confirmation to start planning or read-only discovery. Particular approval actions
still use the actual AGENTS.md protocol when their exact targets are ready.
