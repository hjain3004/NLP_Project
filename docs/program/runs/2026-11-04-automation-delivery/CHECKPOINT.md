# Automation delivery — coordinator checkpoint (2026-11-04)

**Owner of this file:** the coordinator (parent) session only. Workers publish uniquely named
reports under `reports/`. This file adds no authority; AGENTS.md governs.
**Base:** `bc7c369a5b31b91094e48d07a29dfc47daedbd36`. **Branch:** `claude/brave-keller-7gd2bc` (pushed).

## Outcome in one paragraph

Routing, one live provider path (Anthropic `claude -p`, no tools), `run`, `review run`, serialized
`integrate`, and a shared hardened gate runner are implemented and merged. The full cycle was
observed live once for two small real fixes in an isolated delivery clone: concurrent authoring
(about 146 s overlap), verification, fresh-session independent review, unchanged integration.
G0 is **not** closed and cannot be from this environment.

## Task states

| ID | State | Evidence |
|---|---|---|
| Preflight + plan | done | commit `3a1457c`; independent plan review APPROVE_WITH_CHANGES, all 6 blockers adopted |
| FIXTURE-PIN (G0-04 / baseline error) | done, merged | `edc6fdc`; dry G0-01..11 pass |
| Lane A routing/provider/run/review run (+ env allowlist, hardened git, `verify_candidate` on gate_runner) | done, merged | reports lane-a-1..3; independent review: no blocking |
| Lane B `integrate` | done, merged | reports lane-b-1..4; review 1: 2 blocking → repair 1 → re-review still blocking → escalated to Opus repair 3 → review 3: no blocking |
| DEMO cycle 1 | recorded, incomplete | `reports/demo-1.md`: reviewer caught a real defect in DEMO-001's candidate; DEMO-002 rejected at integration (my contract used git-dependent checks) |
| DEMO cycle 2 (DEMO-101/102 replans) | done | `reports/demo-2.md`; integration merge `dccdf89`; merged into the session branch by coordinator `git merge` at `881264f` (**outside the control plane**; commits unchanged) |
| Final architecture audit (fresh Opus) | done | `reports/final-audit-review-input.md` — AUDIT_PASS_WITH_NONBLOCKING; **review input, not a G0-12 artifact** |
| HARDENING-1 (audit F1a/F1b/F3) | done, merged | `reports/hardening-1.md`; independent review found a blank-check hole (`[""]`) and a risk deny-list → one repair `caa5434` → mutation-confirmed |
| Draft PR | opened by coordinator after this commit | see handoff |

Suite at last coordinator check (merged tip): 417 tests OK, 1 skipped (opt-in live test); `docs verify` ok; dry G0 evaluation (11 deterministic criteria) passes.

## Deviations to know

- DEMO-101 fixes `approve.py` (risk `medium`, routed as `standard_implementation`), not the "low-risk
  synthetic" change AUTOMATION_DELIVERY Task 3 asks for. DEMO-102 is risk `low`.
- The delivery clone used a labeled registry profile (`complex_implementation` → `claude-opus-5-5`).
  With the real registry every `standard_implementation` task is `reviewer_unavailable`.
- Integration gates in the demo were each candidate's own new test file; existing suites were dropped
  because they need `git` (verify/integrate environment mismatch). The full suite on the integration
  head passed (404) only in a clean-clone rerun after a Lane B test-fixture isolation problem produced
  23 failures in the first attempt.

## Open findings (none fixed unless stated)

- Candidate/gate code runs at the control-plane uid, before review, with network access; the tamper
  check covers `.git` metadata only; the event store is not protected from gates (audit F1).
  HARDENING-1 added: verify drops sensitive env, empty/blank check lists fail, only low/medium risk may run gates. Proposal (not applied; schema commitment): `minLength`/`pattern` on `deterministic_checks` items.
  Still open: separate uid/network namespace; cross-checking store rows against the event chain.
- Verify (with `.git` link) vs integrate (`.git`-less) environments differ (audit F2).
- Superseded tasks re-enter the integration queue; `repair_required` has no authorize path (audit F4).
- Tamper-defence test gaps; `_finalize` fencing self-comparison; soft `RLIMIT_NPROC`; blob memory (review B3).
- `ingest_audit` accepts self-declared `fresh_session`/`distinct` flags (audit §4); dry-run `gate_result` label
  says `passed` while evaluating only 11 of 14 criteria.
- Lane B asked for two new state-machine rows (pinned 24-row table, design §14.1): not added.

## Blockers needing the owner (cannot be done from the cloud)

1. Decide HERO_SCENARIO: include in level 1 + scope statement, or reclassify (changes the hash to approve).
2. Approve `governance-change-set-v2` (before-hashes still match) and accept ADR-0005 — typed approvals
   need the owner's Mac key/TTY.
3. Provide the G0-12 architecture audit through the control plane (the `architectural_authority` class is
   bound to a provider with no adapter), then G0-13 scope and G0-14 phase approvals, then `agentctl gate G0`.
4. Decide the reviewer-floor registry bindings and the state-machine items above.

## Budget

Platform-enforced ceiling $90; exact platform spend is not observable here. Provider-reported cost of all
live automation calls: about $2.66 (demo cycles) plus about $0.03 of probes and lane smoke tests.
Agent-session spend is not reported to this session.

## Next executable step

Owner: see 'Blockers needing the owner'. Engineering: next work is owner-gated design (reviewer-floor bindings, state-machine rows, gate sandbox uid/network namespace).

## Interrupted-session recovery — 2026-11-05

The published continuation at `f7b3a6e` was recovered. Worker A's unpublished sandbox-history
patch was inspected but not integrated: `runc` is unavailable here, and its required one-commit
test contract conflicts with preserving the Git history needed by `docs verify`. Worker B's
identity repair and its ordinary-test reconciliation were independently reviewed and integrated
as coordinator Git commits `121fd71` and `7354c6e`; no protected tests were modified.

The frozen shadow package was independently reviewed, repaired, and re-reviewed. The integrated
tree is `c288b23`; its linear ancestry includes the identity repair, evaluator hardening, and the
hash-bound freeze marker. The final review verified 47 package hashes, unchanged case packs,
oracles, and answer key, 6/6 evaluator tests, 12 relevant identity/review tests, and zero calls
before the marker. No candidate call has produced a result.

The authorized runner was invoked once with one-call-per-case, no-retry settings and the existing
$1 per-call cap. It refused before the first case because the candidate `claude` CLI is not
authenticated; the champion `openai/gpt-5.6-terra` remains unsupported. This is an external
authentication blocker, not an evaluation result. No credentials were read or passed, and no
retry, promotion, registry change, or G0 action occurred.

The exact Worker C completion branch/commits were not recoverable from local refs or the published
branch; the published lifecycle evidence still records the misleading cancellation chain and its
state-machine amendment remains unactivated.


---

# Operational-readiness repair (continuation of PR #1) — final state

**Branch tip:** see `git log` (final commit is the one that last touched this file). Full suite on the merged code
(`c5ed8f7`, run by the coordinator): **551 tests OK, 1 skipped** (opt-in live test), 0 failures; `docs verify` ok.

| Task | State | Evidence |
|---|---|---|
| 1 Enforced candidate-check boundary | **implemented + independently reviewed; demonstrated on this host only** | `sandbox.py` (runc: new pid/net/ipc/uts/mount ns, read-only host runtime subset, uid 65534, no caps, seccomp, cgroup limits), `docs/architecture/CHECK_ENVIRONMENT.md`; reports `ops-lane-a-1/2`. Review 1 found AF_VSOCK escape + cross-check tree mutation → fixed (also `io_uring`); final fresh Opus review: NO_BLOCKING_FINDINGS, closures verified empirically incl. 32-bit/x32 ABI. Fails closed (`sandbox_unavailable`); only escape hatch is explicit `AGENTCTL_CHECK_ISOLATION=legacy-unsafe` (recorded; refused for risky tasks). |
| 2 One check environment for verify and integrate | implemented; live evidence partial | `check_environment.py`: same materialised tree, env, shell, limits, private synthetic Git repo, fresh tree per check. Git-dependent checks pass in both stages in tests (`OneContractTests`, real runc). Live: OPS-201 verified under it (`git_mode: synthetic-repository`). **Not live:** a git-dependent suite through both stages — OPS-202 never produced a candidate. |
| 3 Real registry usable | usable only with explicit opt-in; **integration blocked by policy** | candidate `claude-opus-5-5` for `complex_implementation` (unevaluated; champion entries byte-identical). Live on the unmodified registry: refusal without opt-in; with `--allow-candidate-bindings` author (Sonnet) and reviewer (Opus) both route. Tasks with a candidate author/reviewer are parked `challenger_not_merge_eligible` (champion-challenger-v1); no waiver flag exists. Owner decision: `reports/registry-owner-decision.md`. |
| 4 Repair / cancellation lifecycle | implemented on existing transitions; tested with a fake provider, one live cancel | `lifecycle.py`, `agentctl task repair|supersede|cancel|status-chain`; races fixed (`authorize_repair` replay). No new state-machine rows. |
| 5 Live acceptance on the real registry | **partial** | `reports/ops-demo-1.md`, `evidence/ops-demo/` (commit `e26681a`): OPS-201 run→sandboxed verify→fresh Opus review→parked at the policy gate, no integration branch. OPS-202 failed twice at authoring (container restart; then an undiagnosed 600 s provider timeout) and was canceled. No concurrent pair this round (earlier delivery demo had ~146 s overlap on the special profile). |
| Final independent review (fresh Opus, merged code) | done | NO_BLOCKING_FINDINGS; open low items below |
| G0 package | prepared | `g0-package/` (README, two validated HERO_SCENARIO patches) |

## Not demonstrated / still open
- **Integration success on the real registry** — blocked until the owner promotes (or rebinds) the `complex_implementation` reviewer.
- Two concurrent disjoint tasks through review/integration on the real registry; live repair lifecycle; live interrupted-integration recovery.
- OPS-202's author timeout is undiagnosed (candidate suspects: large file in the prompt, provider latency). An infrastructure kill consumes one of two attempts.
- `docs verify` cannot be a sandboxed contract check (synthetic repo has one commit); checks can pass vacuously by skipping (e.g. 20/23 skipped).
- Probe skips socket checks if `python3` is absent in the sandbox `/usr` (review, low). `review run` prints `findings: 0` although the stored verdict carries the control-plane finding. Cancel chain records event names for steps that did not literally happen.
- Residuals of the boundary (documented): runc runs as root with no user namespace; host `/usr` readable; `/proc/self/mountinfo` shows host paths; seccomp is allow-by-default; kernel/runc escapes; cgroup v1 and x86_64 only tested; docker backend not implemented. High/critical-risk tasks are refused until a further review and an owner decision.
- Operator identity for `task repair|cancel` is an unauthenticated string, not a human approval.

## G0 exact status
Nothing is recorded. Dry evaluation (`gate G0 --replay-dry-run … --dry-run-only`, writes no state) covers **11 of 14** criteria
(G0-01…G0-11), all pass; its `gate_result: "passed"` label is misleading. G0-12 (audit through the control plane), G0-13, G0-14
are unmet. See `g0-package/README.md` for the ordered owner actions and target hashes.

## Next executable step
Owner decides the `complex_implementation` reviewer binding (`registry-owner-decision.md`) and HERO_SCENARIO option (`g0-package/`).
