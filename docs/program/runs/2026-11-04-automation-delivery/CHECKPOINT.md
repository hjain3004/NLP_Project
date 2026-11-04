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
