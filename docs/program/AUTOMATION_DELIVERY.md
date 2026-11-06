# Automated routing and integration delivery

**Owner request:** 2026-10-22. **Target:** end of 2026-10-22, America/Los_Angeles.
**Status (2026-11-04):** Implemented for one provider path (Anthropic CLI) and observed end to end once in an isolated delivery clone; see `docs/program/runs/2026-11-04-automation-delivery/`. Not multi-provider, not a speedup or schedule claim, and G0 is not closed.
**Status amendment:** Unattended provider execution and automatic candidate collection remain
deferred after the recorded authentication refusal. The supported interim development path
is the existing `ManualProposalAdapter` dispatch/return protocol followed by verifier-owned
checks, independent review, and serialized integration. This does not alter G0 criteria,
active product scope, routing bindings, approval policy, or invariants.
**Baseline inspected:** `5442761372e8fa1b4681705062ba83c98adaa941`.

The target is a working development workflow, not a test-count target. A deadline does
not establish completion or permit bypassing invariants, approvals, or review. The
earlier plan deferred integration to P1 and live adapters to a later automation level;
the owner's current request brings this infrastructure work forward. This document does
not close G0, authorize application development, or retrospectively certify old work.
The supervised interim path above is limited to manually returned, hash-bound artifacts;
it is not evidence of unattended provider integration.

## Observable delivery

An operator starts one eligible synthetic development task through the normal CLI.
The control plane selects its registered model, launches a bounded worker, collects
the result without a manually supplied return file, verifies the actual candidate,
obtains a review from an independent session, and integrates the unchanged candidate
through the serialized queue. The resulting Git tree contains the intended change,
affected checks pass against that tree, and the manifest and event store agree.

The recorded evidence must identify the command, provider and actual model, run/session
identities, starting and resulting commits, verification result, review binding,
integration outcome, elapsed time, and usage when the provider reports it. Missing usage
is reported as unavailable, never zero. No speedup claim is made without a measured basis.

## Global constraints

- Keep the existing scheduler, contracts, leases, context compiler, verifier, and review
  machinery. Implement the missing connections; do not build a second control plane.
- Model identifiers stay in `agent-system/routing/capability-registry.json`. Route from
  capability class and policy, preserving reviewer floors and required independence.
- A missing executable, unavailable model, exhausted budget, or failed authentication
  produces an actionable blocked result. Do not silently substitute a different model,
  claim a fake result is live, or retry without new evidence.
- Use isolated task worktrees and synthetic source only. Do not expose credentials to
  worker context. Do not disable sandbox or approval enforcement to make a launch work.
- Keep deterministic tests offline. A separately identified live development-provider
  smoke run is required for delivery; it is not a product Foundation Models test.
- Integrate only verified, independently reviewed candidates whose exact bindings remain
  current. The deterministic queue selects the candidate. Preserve its commit and patch;
  conflicts or changed bindings produce a repair/replan outcome, not a silent rewrite.
- A failed gate must not advance the integration branch or record successful integration.
  Duplicate requests must not duplicate the change; interrupted integration must be
  reconciled against actual Git and event-store state before another attempt.
- Preserve existing history and unrelated work. No push, history reconstruction, Swift,
  Xcode, UI work, approval bypass, schema change, or protected-test change is implied.
- The coordinator plans and delegates implementation. Use the least costly suitable
  worker; provider availability and task risk must be checked before dispatch.

## Bounded implementation sequence

### Task 1: Route and execute a real worker

Inspect the existing registry, provider CLI capabilities, scheduler dispatch, context,
and manifest ingestion. Freeze the adapter boundary and materialize the exact task
contract before implementation. Replace the routing placeholder and connect one working
provider path through normal CLI execution, with bounded process lifetime, actual session
identity capture, and automatic result collection. Other provider bindings must clearly
report unsupported/unavailable status until they are actually implemented and exercised.
One live provider path proves that path only; it must not be described as complete
multi-provider support.

Acceptance: real registry selection and process execution; a real returned candidate is
ingested with the correct task, context, and lease bindings. Focused checks cover timeout,
nonzero exit, malformed response, and invalid or expired identity/token. Exercise real
process boundaries locally; simulations are explicitly labeled. Do not claim delivery
after this task alone.

### Task 2: Integrate verified candidates

Implement the serialized queue and `agentctl integrate` using current candidate evidence,
review bindings, dependencies, and interface hashes. Use real disposable Git repositories
to exercise successful integration and rejection of an unreviewed or modified candidate,
a stale dependency, a conflict, a failing affected check, and duplicate execution. Verify
the resulting tree and authoritative state, not merely a mocked Git invocation.

Task 2 consumes Task 1's frozen result boundary. Changes needed to shared files are
serialized and scoped in the task contracts. Existing schema or approval-policy changes,
if actually required, must be presented as an exact proposal for the existing approval
mechanism; no speculative governance expansion is part of this delivery.

### Task 3: Connect and demonstrate the complete cycle

Run one low-risk synthetic change through the ordinary CLI with a live provider and an
independent review session, using an isolated delivery repository. No manually fabricated
review verdict or return artifact may stand in for the live boundary. Reuse Tasks 1 and 2's
negative evidence where unchanged, and run affected integration checks on the final tree.
Independent review must assess correctness and whether tests exercise the real behavior.
Record the evidence and update README/roadmap status to match exactly what worked.

## Test quality and cost

Follow AGENTS.md section 8. Every added test names the behavior or failure it protects.
Bug regressions must demonstrate the original failure where reproducible. Avoid tests of
trivial constants, mock configurations, and placeholder exceptions presented as features.
Do not add tests for documentation wording. Run focused checks while iterating; run the
required broader checks at integration, and repeat only when a change or finding warrants it.
Preserve protected checks. Tests of contracts or rejection behavior are valuable when they
protect a real boundary, but do not substitute for a successful workflow.

## Reporting and scope control

At each implementation boundary, report demonstrated behavior, simulated behavior, and
untested or blocked paths separately. If the delivery target becomes infeasible, name the
specific unfinished behavior and evidence-backed blocker promptly. Do not replace it with
another vague completion estimate or silently reduce the agreed outcome.

Adaptive routing benchmarks, dashboards, broad cleanup, historical provenance repair,
and application work are outside this delivery. Existing G0 findings remain recorded and
must not be relabeled as resolved by this plan or by a green automation test suite.
