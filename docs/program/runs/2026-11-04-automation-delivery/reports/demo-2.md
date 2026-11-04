# Demo report 2: automation delivery acceptance run, cycle 2 (replan)

Same delivery clone `/home/user/delivery` and store as cycle 1. BASE `2df58e85e8fc3b8db8346791f2795bc67f21ccd2`.
New delivery commit `df8fbc0` (work package `DELIVERY-DEMO-2`, tasks `DEMO-101`, `DEMO-102`). Cycle 1 evidence in `evidence/demo/` is untouched;
this cycle's evidence is in `evidence/demo-2/` (`commands.log`, `summary.json`, `run-records/`, `contracts/`, `outputs/`, `gitless-precheck-base.txt`).

## Result

**The cycle worked: both candidates were produced live, verified, independently reviewed with no blocking findings, and integrated unchanged.**
`DEMO-101` and `DEMO-102` are `completed`; `agentctl/integration` = `dccdf896094ed6cd2f648ce886953acf26c7a7e8`.
One anomaly, described below, concerns the stale cycle-1 task `DEMO-002`, not the new tasks. A first full-suite run on the integration head failed 23 tests
(a Lane B test-fixture assumption, not the candidates); a rerun in a clean clone passed 404/404.

| Step | DEMO-101 (approve containment, replan of DEMO-001) | DEMO-102 (context runtime exclusion, replan of DEMO-002) |
|---|---|---|
| a. `ready --json` | ready | ready |
| b. `run` (live, concurrent) | exit 0, `89f3bb3958abd9fc4efa769e63d61fb1b46eaf76` | exit 0, `cb5c73159df9097cb3374a28803f0b65c0f05c58` |
| c. `verify` | passed (1 check) | passed (1 check) |
| d. `review run` (fresh Opus session) | `no_blocking_findings` (F1 minor, F2/F3 nit) | `no_blocking_findings` (2 minor) |
| e. `integrate` | `integrated`, fast-forward, `89f3bb3` | `integrated`, `merge_no_ff`, `dccdf89` (parents `89f3bb3`, `cb5c731`) |
| `status` | `completed` | `completed` |

Queue sequence (each exit code recorded in `commands.log`): `integrate` #1 integrated DEMO-101 (0); #2 picked the stale cycle-1 **DEMO-002** and rejected it,
`affected_check_failed` (1); #3 integrated DEMO-102 (0); #4 picked DEMO-002 again and rejected it, `conflict_repair_required` (1; it now conflicts with DEMO-102
on `context.py` and the test file); #5 `queue_empty` (3, DEMO-002 parked without new evidence).

### Agreement checks (step f)
- `git log --graph --oneline agentctl/integration`: DEMO-101 fast-forwarded onto BASE, DEMO-102 merged with a merge commit; `git diff --stat BASE..integration` touches exactly the 4 intended files (2 production, 2 new tests; +329/-5).
- Candidate commits are reachable from the integration head and equal the manifests' `result_commit` and the `agentctl/DEMO-10x/attempt-1` branch tips. `git patch-id` of the DEMO-102 candidate equals the patch the integrator applied (`c2b540f4...`); DEMO-101 is the same commit (fast-forward). Both integration manifests state `candidate_patch_unchanged: true`.
- `agentctl validate` passes on both integration manifests and on each task's context manifest, implementation manifest and review verdict (in the attempt directories). The copies of the implementation manifest and verdict inside the integration record directory do not validate standalone (their context manifest is not in that directory); this is a property of the record layout, noted, not investigated.
- Event store: 57 events in total, cycle 2 = events 27-57 (leases for both tasks at 30/31, the two author runs interleaved, integration leases and passes at 52/53 and 55/56; the two extra DEMO-002 rejections at 54 and 57).
- Full suite on a detached worktree of the integration head, one suite at a time:
  - Run 1, worktree inside the delivery clone: 404 tests, **23 failures**, 1 skipped. All 23 are in Lane B's disposable-repository tests (`test_integrate.py`, `test_integrate_hardening.py`, `test_integrate_queue.py`) with one cause: the fixture clones the worktree with `git clone --shared`, and a worktree detached exactly at a branch tip makes git take that branch (`agentctl/integration`) as the clone's HEAD, so the fixture sees a branch it expects not to exist. I reproduced the clone behaviour directly. This is a fragility of the Lane B test fixture, newly exposed by a real integration branch, not a failure of the candidates.
  - Run 2, detached worktree at the same commit taken from a clean clone (no local branch at that commit): **404 tests, OK, 1 skipped** (176 s). The expected count is 391 on BASE plus 8 and 5 new tests.
- Regression evidence (AGENTS.md section 8), `outputs/regression-new-tests-vs-unfixed-base.txt`: against the unfixed BASE source in a `.git`-less copy, 2 of the 8 approve tests error and 4 of the 5 context tests fail. Against the blocked cycle-1 DEMO-001 candidate source the new `relative_to` test errors with the very `ValueError` the cycle-1 reviewer predicted. So the new test would have caught the cycle-1 defect.

## Why this cycle was legitimate and what changed in the contracts
New bounded replan tasks (AGENTS.md section 9), new ids, `DEMO-001/002` rows not edited. Contract rules applied: (1) every check must pass in a `.git`-less copy of BASE. I ran all 33 existing test files there (`gitless-precheck-base.txt`): only `test_claude_cli_adapter`, `test_dag`, `test_governance` pass, and none exercises `approve.py` or `context.py`; every suite that does (`test_approval*`, `test_context_staleness`, `test_plan_loading`) errors at BASE without `.git`, so all were **dropped**. Each task keeps a single check, its own new test file; the broader regression net is the full-suite run above, not the gate.
(2) Acceptance behaviors were written from the command's angle, with the cycle-1 review finding quoted in substance in DEMO-101's objective. The approve tests drive `run(argv)` with `--preflight` (feasible without git by patching `load_config` to a real temporary root holding copies of the policy files; I probed this first) and apply `destination.relative_to(root)` to the helper's return, which is exactly what `run()` does. The issuance and `--recover-output` paths themselves are not driven (they need a TTY or a registered approval); the reviewer notes this (F3).

## Demonstrated vs simulated vs not run
**Demonstrated (live provider, real CLI, real git, real event store, ordinary CLI throughout):** registry-driven selection (Sonnet 5 authors, Opus 5.5 reviewers under the labelled demo profile); two concurrent live `run`s with correct task/context/lease bindings; `verify` on the actual candidates in the shipped sandboxed gate; independent reviews by distinct sessions (machine-attested); the reviewer-driven replan loop (cycle 1 finding -> new contract -> fix that passes review); serialized integration with the intended tree on the integration branch, unchanged candidate patches and passing affected gates; the integrator rejecting a stale candidate (twice) without advancing the branch; the queue parking it.
**Simulated:** nothing in this cycle. The reviewer class on Anthropic is the labelled delivery-clone profile (not a registry change for main).
**Not demonstrated:** duplicate-request idempotence; recovery from an interrupted integration; non-Anthropic providers; the approve issuance and recovery paths end to end (TTY/registered approval); that DEMO-102's discovery pruning actually avoids traversing `runtime/` (its test shows runtime files are not opened, the reviewer's F2). The candidates' quality was judged by one Opus review each plus the tests; the prior cycle shows a review can find what tests miss, so this is evidence, not proof.

## Manual interventions
**0 in the run flow.** Every `ready`, `run`, `verify`, `review run`, `integrate`, `status` was the ordinary CLI; no candidate, verdict, id, store row or production file was hand-edited.
Setup (not counted): the `df8fbc0` contracts commit and the `.git`-less pre-check of existing tests. Read-only diagnostics: the `.git`-less probe and the regression runs, the two suite runs, a clone probe, the bundle. One operator slip: an event-export command wrote its output to `/home/user/NLP_Project/x` by a path typo; I deleted that untracked file at once and nothing else in that checkout was touched (noted in `commands.log`).

## Timings and cost
Timestamps are `date +%s.%N` around each command (`outputs/*.start|end`); elapsed values are from the provider records.

| Item | Value |
|---|---|
| `run DEMO-101` window | 306.99 s (record elapsed ~307 s) |
| `run DEMO-102` window | 145.98 s |
| **Overlap of the two run windows** | **145.98 s** (DEMO-102's whole run lies inside DEMO-101's) |
| Sum of the two author windows if serial | 452.97 s versus 306.99 s observed for both |
| Review windows / overlap | 23.90 s and 17.54 s / 17.54 s |
| First run start -> last review end | 343.36 s |
| First run start -> last `integrate` end | 368.43 s |

| Provider-reported cost (USD) | Author (Sonnet 5) | Reviewer (Opus 5.5) |
|---|---|---|
| DEMO-101 | 0.634334 (105,662 in / 39,816 out) | 0.272735 |
| DEMO-102 | 0.288639 (29,918 in / 18,479 out) | 0.232431 |
| Subtotal | 0.922973 | 0.505166 |
| **Cycle 2 total** | **1.428139** | |

Cycles 1 and 2 together: $2.660664. All four calls reported usage. Result commits integrated: `89f3bb3958abd9fc4efa769e63d61fb1b46eaf76`, `cb5c73159df9097cb3374a28803f0b65c0f05c58`; integration head `dccdf896094ed6cd2f648ce886953acf26c7a7e8`; bundle `/home/user/wt/demo-integration.bundle` (verified, outside every repository tree, not committed).

**No 4x speedup claim is made and no project completion date is implied.** Two live runs overlapped by 146 s on this host and completed; that is the only timing fact established, for two small tasks, one sample.

## Open issues
1. **Stale tasks keep re-entering the queue.** Every advance of the integration head un-parks the rejected cycle-1 `DEMO-002`; the integrator retried it twice and wrote two more rejection artifacts and events (no state change, branch not advanced). A rejected candidate has no terminal state, so a superseded task should be cancelable by an authorised path. This also means "untouched" rows are untouched only in state, not in the event log.
2. `verify` still runs checks in a worktree with a `.git` link while `integrate` runs them without one; cycle 1's failure mode is not prevented, only avoided by the contract author (see the pre-check). Contract validation or `verify` should catch it.
3. Lane B's test fixture breaks when the repository that hosts the suite has an `agentctl/integration` branch at the detached HEAD (23 failures). Suggest isolating the fixture from the host's refs (for example `git clone --no-local --branch <name>` or checking out a named branch before cloning).
4. Reviewer notes carried forward (non-blocking): DEMO-101 returns a path anchored on `root.resolve()` and assumes a resolved root; a final component of `..` is accepted and fails only at write time; DEMO-102's pruning is lexical and its test shows non-opening, not non-traversal.
5. The demo registry profile remains delivery-clone-only; the real registry still refuses every standard task (`reviewer_unavailable`). `repair_required` still has no authorize path.
6. Integration-record copies of the implementation manifest and review verdict do not validate standalone (missing sibling context manifest).
