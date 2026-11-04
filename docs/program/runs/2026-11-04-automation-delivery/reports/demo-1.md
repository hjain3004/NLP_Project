# Demo report 1: automation delivery acceptance run (DEMO operator)

Delivery repository: `/home/user/delivery` (isolated clone, never pushed; the `origin` remote was removed).
BASE `2df58e85e8fc3b8db8346791f2795bc67f21ccd2`. Delivery commits: `d24f481` (registry demo profile),
`5cba2e8` (DELIVERY-DEMO work package and contracts). Evidence: `evidence/demo/` next to this report's directory
(`commands.log`, `summary.json`, `negative-control.txt`, `run-records/`, `contracts/`, `outputs/`).

## Result

**The cycle did not complete. Nothing was integrated.** It failed at step e (integrate) for DEMO-002 and
at step d (independent review) for DEMO-001.

| Step | DEMO-001 (approve containment) | DEMO-002 (context runtime exclusion) |
|---|---|---|
| a. `ready --json` | ready | ready |
| b. `run` (live, concurrent) | exit 0, candidate `93d1ddcc` | exit 0, candidate `6a682e46` |
| c. `verify` | passed (state `independent_review`) | passed |
| d. `review run` (fresh Opus session) | **`blocking_findings`**, state `repair_required` | `no_blocking_findings`, state `integration_ready` |
| e. `integrate` | not eligible (never reached the queue) | **rejected, `affected_check_failed`**, task stays `integration_ready`; second `integrate` exit 3 `queue_empty` (parked without new evidence) |
| f. integration branch | `agentctl/integration` does not exist | |

I stopped at each failure, did not repair anything, did not re-run any task (0 of the 1 allowed re-runs used), and spent no
further provider budget. Provider-reported cost for the whole run: **$1.2325** (cap about $6).

### Why DEMO-001 stopped (a genuine candidate defect, caught by the review)

The independent reviewer (`claude-opus-5-5`, session `0797b363-...`, distinct from the author) returned one blocking finding (F1) and one
non-blocking (F2). The candidate drops the lexical `destination.relative_to(root)` check, so `_contained_destination` now accepts an alias
path, but it returns the path **unresolved**. `approve.py`'s `run()` then calls `destination.relative_to(config.repo_root)` on that value in the
issuance and `--recover-output` paths, which raises an uncaught `ValueError`. So the helper accepts the input, and the command still fails on it.
The candidate's own test only re-applies `.parent.resolve()` to the returned value, so it cannot see this (F2).
I confirmed F1 independently on the candidate commit with a real symlink (`outputs/diag-demo001-F1-poc.txt`): the helper returns
`.../alias-repo/sub/out.json` and `relative_to(real repo root)` raises `ValueError`. This is the verification-versus-review split working as
intended: the deterministic checks passed and the review caught a real behaviour bug.

The task has one attempt left (`maximum_attempts: 2`) but is in `repair_required`, which has no authorize path (known open item). It stays there.

### Why DEMO-002 was rejected at integration (a defect in my contract's check selection, not in the candidate)

`integrate` runs the contract's checks in a `.git`-less copy of the planned tree. Two of DEMO-002's three checks failed there
(exit 1): `-p test_context_staleness.py` and `-p test_plan_loading.py`. Both existing suites call `git rev-parse --show-toplevel` at test
time (`test_context_staleness.py:24`, `test_plan_loading.py:21`), which exits 128 without a `.git`. I reproduced this on the **BASE tree** in a
`.git`-less copy under a minimal environment: the same suites error identically, so the candidate did not cause it. (The gate record keeps only
stderr hashes, so the diagnosis is my reproduction, not a captured stderr.) `test_approval*.py`, DEMO-001's second check, has the same property
(3 errors at BASE), so DEMO-001 would have been rejected here as well even with a clean review. The brief asked for checks with "NO git commands";
the selected existing suites were not checked against that. The new test file's own check passed in the integration copy.

Two consequences worth an owner decision:

1. `verify` runs checks in the leased worktree (has a `.git` link) and passed the same commands that the integrator later rejects. A check that
   can never pass at integration is not detected until integration. Either `verify` should run in the integration condition, or contract
   validation should flag checks that need git.
2. After a rejection the task stays `integration_ready` and has no legal exit (no new transition was added); the queue parks it. The only way
   forward is a new contract revision/task, with fresh verification and review. I did not do that.

## Demonstrated vs simulated vs not run

**Demonstrated (live provider, real CLI, real git, real event store, all through the ordinary CLI, no hand-edited candidate, verdict or id):**
- Real-registry negative control: `run` refused with `reviewer_unavailable` (reviewer `complex_implementation` unsupported_provider), exit 3, no lease, no call, no cost (`negative-control.txt`).
- Registry-driven selection: `standard_implementation` -> `anthropic/claude-sonnet-5` (served model `claude-sonnet-5`, no extra models); reviewer `complex_implementation` -> `claude-opus-5-5` under the labelled demo profile.
- Two live `run` processes concurrently: both leased, ran tool-less, were scope-audited, committed by the control plane and ingested with correct task/context/contract/lease bindings (events 4-13 interleave).
- `verify` on the actual candidates (2 and 3 checks pass respectively, evidence hash recorded); `review run` with a fresh reviewer session each, independence attested by machine identity (`distinct: true`).
- A live reviewer found a real behavioural defect that the candidate's tests missed (DEMO-001).
- The integrator rejected a candidate on an affected-check failure without advancing a branch or recording success, and refused to retry it without new evidence.

**Simulated:** nothing in this run is simulated. The registry profile is a labelled environment substitution (reviewer class on Anthropic), not a simulation of a provider.

**Not demonstrated / not run:** a successful integration (no `agentctl/integration` branch, no integration-manifest, no tree containing the change);
duplicate-request idempotence; the full integration-tree suite required by step f on an integration head (none exists); `agentctl status` showing
`completed` for either task; the coordinator bundle (not created, since the run did not work). Diagnostics I ran instead, clearly **not** integration evidence:
the full agentctl suite on each candidate commit in a detached worktree with a `.git`: DEMO-002 candidate 394 tests OK (1 skipped, 183 s), DEMO-001 candidate 395 tests OK
(1 skipped, 179 s). A first run of both suites at the same time failed 2 `test_gate_runner` timing tests on each (resource contention between the two
suites); `test_gate_runner.py` alone passes 15/15 on both, and the sequential runs are clean. DEMO-001's passing suite does not contradict F1: the suite never exercises the command path.

## Manual interventions

**0 in the run flow** (target met): every `run`, `verify`, `review run`, `integrate`, `ready`, `status` was the ordinary CLI, no store or candidate edit, no retry.
Setup, not counted as intervention: the clone, the demo registry profile commit (labelled delivery-repo-only), the work package and two contracts, the evidence worktree.
Read-only operator diagnostics after the failure (D1-D3 in `commands.log`): `.git`-less copies from `git archive`, a PoC script, and full-suite runs in two temporary detached
worktrees (removed afterwards; they briefly appeared in the delivery clone's worktree list). The contract design error above is an operator error in setup and is reported, not repaired.

## Timings and cost

All timestamps are `date +%s.%N` taken around each command (`outputs/*.start|end`); run-record elapsed times come from the provider records.

| Item | Value |
|---|---|
| `run DEMO-001` window | 1793774690.785 -> 1793774872.367 = 181.58 s (record elapsed 181.03 s) |
| `run DEMO-002` window | 1793774690.785 -> 1793774823.936 = 133.15 s (record elapsed 132.63 s) |
| **Overlap of the two run windows** | **133.15 s** (the whole DEMO-002 run lies inside DEMO-001's) |
| Review windows | 24.44 s (DEMO-001), 17.14 s (DEMO-002); overlap 17.14 s |
| First run start -> last `integrate` end | 282.72 s |
| First run start -> last review end | 232.09 s |
| Sum of the two author run windows (serial equivalent) | 314.73 s versus 181.58 s observed for both |

| Provider-reported cost (USD) | Author (Sonnet 5) | Reviewer (Opus 5.5) |
|---|---|---|
| DEMO-001 | 0.381810 (41,076 in / 21,751 out) | 0.248248 (25,572 in / 2,184 out) |
| DEMO-002 | 0.365011 (69,855 in / 19,665 out) | 0.237456 (25,908 in / 1,510 out) |
| Subtotal | 0.746821 | 0.485704 |
| **Total** | **1.232525** | |

Input tokens are `input + cache_creation + cache_read` as the adapter defines them. Usage was reported by the provider for all four calls (none unavailable).
Result commits (not integrated): DEMO-001 `93d1ddcc42bf5ef3bbb2b1e0c11d73873a8331a2`, DEMO-002 `6a682e461f75eebd733ccf5937bf2f85da9d31c8`.

**No 4x speedup claim is made. No project completion date is implied.** The measured overlap shows only that two live runs can proceed at once on this host;
it says nothing about end-to-end speedup, because neither task completed.

Run identities (full list in `summary.json`): author sessions `542ffc44-...` (DEMO-001) and `4a40799b-...` (DEMO-002); reviewer sessions `0797b363-...` and `a55f420c-...`.
Contract hashes `df44a08a...` and `c3bc6006...`; context hashes `84026949...` and `62b1ae0b...`. 26 events in the store.

## Open issues

1. **Contract check selection for the integrator.** Checks that call `git` cannot pass in the `.git`-less integration copy. Needs either checks that do not need git (or tests that find the
   repository root without `git rev-parse`), or `verify` running under the same condition. Until then, DEMO-001/002 cannot integrate.
2. **A rejected candidate has no exit** (`integration_ready` with a parking artifact) and `repair_required` has no authorize path: both tasks are dead ends for this contract. Re-doing the work needs a new contract/task and a new budget decision.
3. The DEMO-001 candidate is not acceptable as is (F1). A rewrite should resolve the parent and return `parent.resolve() / name`, and its test must go through a caller-level path. A second attempt would have to carry the finding as new evidence.
4. DEMO-002 reviewer notes (non-blocking): the "no contract" test asserts a bare `PlanningError`; discovery still lists runtime files before filtering. The review is otherwise clean.
5. The demo registry profile is delivery-clone-only; the real registry still refuses every standard task (`reviewer_unavailable`). That remains an owner decision.
6. A coverage limitation of the reviewer evidence: one review per task; whether another reviewer would reach the same verdict on DEMO-002 was not tested.
7. Full-suite runs of two candidates at once make two gate-runner timing tests fail (contention); the suite should not be parallelised on this host.
