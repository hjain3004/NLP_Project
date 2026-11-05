# Ops demo report 1: operational-readiness acceptance run on the real registry (DELIVERY-OPS)

Operator: acceptance operator (a Claude Code session). Delivery clone `/home/user/delivery2`, branch `delivery-ops`, no remote.
BASE `c5ed8f7a783242167657d4a9d7343c55f9c721a6`; the work-package commit is `84c7fca64ae1707ca10d16885fdd7bac9c2cd4e9` (contracts keep `base_commit` = BASE).
Evidence: `evidence/ops-demo/` next to the run (`commands.log`, `summary.json`, `contracts/`, `negative-control/`, `run-records/`, `outputs/`).
This report is a record. It adds no authority and claims no approval.

## Result

**The cycle ran as far as the enforced policy lets it, and stopped there. Integration success on the real registry is NOT demonstrated.**

| Step | OPS-201 (integrator wording) | OPS-202 (materialiser size limits) |
|---|---|---|
| 0. `run` without opt-in (negative control) | refused, exit 3, `reviewer_unavailable` / `unsupported_provider`; message names the candidate `anthropic/claude-opus-5-5`, `--allow-candidate-bindings` and `AGENTCTL_ALLOW_CANDIDATE_BINDINGS=1`, says no lease is issued | not run |
| a. `ready --json` | ready | ready |
| b. `run --allow-candidate-bindings --max-budget-usd 1.5` (live) | exit 0, candidate `0663c4c4ee126f30dfcfda8ee083696120957040`, 1 file changed, 29.4 s, Sonnet 5 | **attempt 1: killed by a container restart at 260 s (SIGTERM, `KeyboardInterrupt`, no usage). Attempt 2 (the one coordinator-directed re-run): provider timeout at 600 s, no output, exit 1, no usage. Attempts exhausted (`attempts_remaining` 0).** |
| c. `verify` | exit 0, 3/3 checks pass under `isolation: runc`, 1.3 s | not reached |
| d. `review run --allow-candidate-bindings` | exit 0, `no_blocking_findings`, fresh Opus 5.5 session, 6.3 s | not reached |
| e. `integrate` (no id) / `integrate OPS-201` | `queue_empty` (exit 3) with `parked_without_new_evidence: {OPS-201: challenger_not_merge_eligible}`; explicit: refused (exit 1) `challenger_not_merge_eligible`. **Expected legitimate stop. No flag, store edit or workaround was tried.** The branch `agentctl/integration` does not exist. | n/a |
| f. lifecycle | | `task status-chain` (read-only), then operator `task cancel` (see below) |

Why it stopped: the only reviewer that can run (`claude-opus-5-5`) is a disclosed, unevaluated `candidate` binding; `champion-challenger-v1` makes a challenger-reviewed task merge-ineligible; the task is correctly left `integration_ready` and parked. Owner promotion of the binding is required.

Candidate diff (`outputs/candidate-OPS-201.diff`): adds one sentence ("The deterministic integration queue selects the next candidate, not a model and not the integrator; the integrator consumes the head of that queue.") and replaces the Owns bullet with "consuming the head of the deterministic integration queue". Every other line is unchanged. `agentctl docs verify`, run by me as a read-only diagnostic in a scratch clone with full history, passes on the candidate (`status: ok`, 82 files).

## Contract decisions the coordinator must know (checks dropped)

I ran each proposed check on BASE through the product's own `run_candidate_checks` (`outputs/precheck-base-candidate-checks.txt`) and diagnosed each failure by running the same command in the sandbox:

| Proposed check | On BASE inside the sandbox | Decision |
|---|---|---|
| `agentctl docs verify` (required for OPS-201 by the brief) | **fails**: `_verify_banner_shape` runs `git show f29338c^:BOOTSTRAP.md`; the synthetic repository holds a single snapshot commit | **dropped from OPS-201** (environmental). Run outside the gate as a diagnostic instead (passes on the candidate). |
| `test_docscheck`, `test_validate_valid` | fail, same cause (history older than the snapshot commit) | dropped (never proposed in contracts) |
| `test_review_independence` | fails: needs runc inside the sandbox (`sandbox_requires_root`) | dropped |
| `test_check_environment` | "passes" with 20 of 23 tests skipped (runc unavailable inside) | dropped as vacuous |
| `test_plan_loading`, `test_context_staleness` (git-dependent) | pass | **kept in OPS-202** |
| `test_hashing/dag/events/store/models/locks/approval_prebind/program_dag/jsonschema_subset/validate_invalid` | pass | not used |

OPS-201 final checks: grep-require (`deterministic` near `queue`), grep-forbid (old bullet), and one git-dependent check (`git rev-parse --show-toplevel` = `/work`, `git ls-files --error-unmatch`, plus two grep guards that the May-not and independence rules are untouched). On BASE: 1 fail, 1 fail, 1 pass, as intended. OPS-202 checks: the new test file (exit 5 on BASE, file absent), plus the two suites above (pass on BASE).
The brief called for `docs verify` as a check. Dropping it is a deviation I made on the brief's own stated principle; the consequence is that the project's docs gate cannot currently be a contract check at all.

## Demonstrated vs simulated vs not run

**Demonstrated (live provider, real CLI, real Git, real event store, real runc sandbox, ordinary CLI throughout):**
- registry-driven selection under the REAL, unmodified registry (bytes `cmp`-identical at start, after the restart and at end): Sonnet 5 champion author, Opus 5.5 candidate reviewer only with the explicit opt-in; refusal without it, naming the flag;
- two concurrent `run`s launched together (leases 4 and 6 interleaved in the event log); OPS-201 completed inside OPS-202's first window (29.4 s overlap) - but OPS-202's first run never completed;
- `verify` in the runc check environment: `isolation: runc`, `network: none`, `git_mode: synthetic-repository`, seccomp/capabilities/memory/pids/cpu limits enforced, 3 checks ran;
- independent review by a fresh session: `independence_attestation.distinct: true`, author and reviewer session and run ids all different (below); `CP-CANDIDATE-BINDING-1` (non-blocking, "[control-plane note, not a reviewer finding]") stored in the verdict; `review run` printed `reviewer_binding_type: candidate`;
- the integrator's policy gate on the real flow: `challenger_not_merge_eligible` by both the queue form and the explicit form, no branch created, no integration row, no new events;
- lifecycle: `task status-chain` (read-only plan), `task cancel` (4 recorded steps), and `integrate` afterwards unaffected.

**Simulated:** nothing in this run. (Repair lifecycle, the hostile-check sandbox demonstrations, mocked-provider failure injection and the queue-exit of an `integration_ready` task live in the test suite with a fake provider; none of that was exercised here and none of it proves the live provider path.)

**Not demonstrated / not run:**
- **Integration of a candidate on the real registry** - blocked by policy (above); the integration merge, affected-gate rerun and unchanged-patch check were not run.
- OPS-202: no candidate, no verification, no review. The materialiser change and its tests do not exist.
- Overlap of two reviews (only one review existed).
- The lifecycle "queue exit" is weaker than the brief intended: OPS-202 was `ready` (never `integration_ready`), so it was never queue-eligible and "no longer eligible" after cancel is trivially true. What is shown is that the cancel walks the chain, history is preserved and `integrate` is unaffected.
- Duplicate-request idempotence, interrupted-integration recovery, non-Anthropic providers.

## Agreement checks (step g; `outputs/agreement-*.txt`)
- Event store vs Git: no `agentctl/integration` ref; `integrations` table has 0 rows; no `integration_*` event; `delivery-ops` HEAD still `84c7fca`. The only refs are `agentctl/OPS-201/attempt-1` (`0663c4c`), `agentctl/OPS-202/attempt-1` and `-2` (both still at BASE).
- Candidate commit `0663c4c` is the stored manifest's `result_commit`, the `attempts.result_commit`, the review's `reviewed_result_commit` and the branch tip; its parent is BASE; one commit, one file (`agent-system/roles/integrator.md`), tree `e2e43dcc...`.
- Identities are distinct: author session `4aceff97-b79d-41be-9910-f4c65277c017`, author run `d6b81448-f3e3-4061-bb22-86eed7b78a45`; reviewer session `974ac7c1-426f-45d6-aa24-58cb4d76319c`, reviewer run `5ae2973b-b806-4cc6-9ee1-e91b3b3cf8bd`; roles `standard_implementation` vs `complex_implementation`; served models `claude-sonnet-5` / `claude-opus-5-5`, `extra_models: []`.
- Registry bytes unchanged (`outputs/registry-cmp.txt`); `runc list` empty at the end.
- Event counts monotonic: 21 total (8 for OPS-202) before cancel, 25 (12) after; a subsequent `integrate` added none.

## Manual interventions
- **In the run flow: 0.** No candidate, verdict, id, store row, registry or production file was edited.
- Labeled operator action: `task cancel OPS-202 --reason "acceptance: demonstrate queue exit"` (brief step f).
- Coordinator-directed: the single re-run of OPS-202 after the restart (the brief's "one re-run after a diagnosed infrastructure failure").
- Read-only diagnostics, not interventions: direct sqlite reads of the store; the scratch-clone `docs verify`; one extra explicit `integrate OPS-202` after the cancel (refused `task_not_integration_ready`, no state change).
- Setup (excluded): clone/branch, contracts, BASE prechecks, validate, package commit, `plan validate`.

## Infrastructure event
The container restarted during the first concurrent author runs. OPS-202's `run` received SIGTERM at 260.2 s and ended `failed/KeyboardInterrupt` (run record preserved: `run-records/runrecord-OPS-202-attempt-1.json`); its lease expired through the existing `lease_expired` path and the task returned to `ready`. Disk state survived; the sandbox probe was available afterwards. That is the diagnosis that licensed the one re-run.
The re-run then failed differently (provider timeout, 600 s, no output). **That second failure is NOT diagnosed as infrastructure** (OPS-201's live call in the same environment took 29 s, so the provider path was working; OPS-202's prompt is 81,643 bytes against OPS-201's 38,053, and both OPS-202 calls produced no result inside 260 s and 600 s). I did not retry, per the no-retry-without-evidence rule and because the contract ceiling (`maximum_attempts: 2`) is spent. Cause unknown; I do not claim it was the task size.

## Timings and cost
Epoch seconds (`date +%s.%N`); windows from the CLI wrapper, provider times from run records.

| Item | Value |
|---|---|
| `run OPS-201` | 1793836816.259 to 1793836845.694 = 29.43 s (provider 28.78 s) |
| `run OPS-202` attempt 1 | started 1793836816.260, killed after 260.194 s (record) |
| **Author overlap** | 29.43 s (OPS-201's whole run inside OPS-202's first window); only one of the two completed. The OPS-202 re-run overlapped nothing. |
| `run OPS-202` attempt 2 | 1793837135.361 to 1793837735.902 = 600.54 s, timeout |
| `verify OPS-201` | 1.32 s |
| `review run OPS-201` | 6.27 s (provider 5.79 s; 293 output tokens) |
| First run start to last review end | 986.6 s (includes the restart gap and the 600 s failed re-run) |
| First run start to last command end | 1009.4 s |

| Provider-reported cost (USD) | |
|---|---|
| OPS-201 author (Sonnet 5; 22,918 in / 3,067 out) | 0.122338 |
| OPS-201 reviewer (Opus 5.5; 19,549 in / 293 out) | 0.162244 |
| **Known total** | **0.284582** |
| OPS-202 attempts 1 and 2 | **null** (no usage reported; true spend unknown, not zero; each call capped at `--max-budget-usd 1.5`) |
| Upper bound from per-call caps | 3.285 (inside the ~$4 budget) |

No speedup or completion-date claim is made. The negative control, `ready`, `verify`, `integrate` and `cancel` calls made no provider call.

## Open issues
1. **No real-registry integration path exists until the owner decides the `complex_implementation` binding** (promote / evaluate / bind elsewhere). The gate behaved exactly as designed; the cycle cannot be closed from here. A promoted binding requires fresh review evidence (`challenger_not_merge_eligible` text).
2. **`docs verify` cannot run in the check environment** (needs git history; the synthetic repository has one commit), so docs-only tasks cannot carry the project's own docs gate as a contract check. Likewise `test_docscheck`, `test_validate_valid`. This is a gap, not a bug in this run.
3. **A passing check can be a skip-pass**: `test_check_environment` exits 0 with 20 of 23 tests skipped inside the sandbox. Contract authors can accidentally pick a vacuous check; the precheck caught it only because I looked.
4. **An infrastructure kill consumes an attempt**: OPS-202's attempt 1 was counted though no provider result existed; with `maximum_attempts: 2` the restart halved the budget.
5. **OPS-202's undiagnosed long-running author call** (two attempts, no output at 260 s and 600 s) - needs a human look (prompt size, `--timeout`, provider side), not a blind retry. The task is now canceled; a replan is the lifecycle route.
6. `review run` printed `findings: 0` while the stored verdict contains the non-blocking control-plane finding `CP-CANDIDATE-BINDING-1` (1 finding). Cosmetic, but a reader of the CLI output alone will miss the disclosure.
7. The cancel chain from `ready` records `dependency_changed`, `context_rebuilt`, `contract_invalid`, `replacement_plan_accepted` although none of those occurred (known lane B item D4); the reason code `operator_canceled` and the `task-cancellation:OPS-202` decision artifact are what distinguish it.
8. One 6-second Opus review of a 4-line documentation change is one sample of the candidate reviewer; it is not an evaluation.
