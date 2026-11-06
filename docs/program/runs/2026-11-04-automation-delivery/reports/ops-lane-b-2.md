# Ops Lane B report 2: candidate bindings are not merge-eligible; routing scope; registry validation

Worktree `/home/user/wt/life`, branch `claude/ops-lifecycle`, from tip `26fdad9`. Commits (none pushed): `4654f4b` (fixes 2 and 3),
`2e3fe83` (fix 4), `766b015` (fix 1, registry scope text, isolation pin). This report is a separate docs-only commit after them.
No lease, epoch or fencing token was issued for this work; there is no leased-attempt artifact to echo.

**Full suite:** `make -C tools/agentctl test`, one run, from branch `claude/ops-lifecycle` at `766b015`: **494 tests, 1 skipped (the opt-in live-provider test), 1 failure**, 346 s
(report 1: 472 tests; this lane adds 22). The failure is `tests.test_lifecycle.RepairConcurrencyTests.test_parallel_identical_authorizations_produce_exactly_one`
(a child process exited 3 = `LifecycleRefused`; the assertion message did not carry the reason). It is not reproduced: it passed 3 times alone, 6 times under 4 CPU-burner processes, and
in a focused rerun of `RepairConcurrencyTests` + `RepairRefusalTests` (10 tests OK). I did not touch `lifecycle.py` or that test, so this is a pre-existing timing-dependent test (six racing processes with a
0.5 s start barrier) that failed once on a host that had just come up. No second full suite was run. Treat the suite as green except for that one unreproduced flake, and re-run it if that matters.

## FIX 1 (blocking): a candidate-approved or candidate-authored task is not merge-eligible

`integration_queue.py`. `build_queue` now parks an `integration_ready` task as `challenger_not_merge_eligible` when `candidate_binding_roles()` finds, in **stored** rows for the task's latest attempt:
- reviewer: the approving review (`verdict = no_blocking_findings`) carries finding `CP-CANDIDATE-BINDING-1`, **or** the reviewer's `model_runs` row (joined by `reviewer_run_id`) has `binding_type: candidate`;
- author: the implementation manifest has a `deviations` line starting `[CP-CANDIDATE-BINDING-1:author]`, **or** the author's `model_runs` row (joined by `attempts.author_run_id`) has `binding_type: candidate`.

The registry file is never read, so a later registry edit cannot re-qualify an old review (tested: the candidate entry is removed and the champion re-bound; the task stays parked). Evidence about earlier attempts is ignored.
`execute.py` now writes the two new stored facts (`binding_type` on every `model_runs` summary; the marker on the author deviation); the verdict finding already existed.

Behaviour (real Git, store and `agentctl integrate` processes):
- State stays `integration_ready` (review passed); the task is not eligible and is never the queue head, so a champion-reviewed task alongside it is integrated (tested, both orders).
- Parking writes nothing: no rejection artifact, lease or event on any call, however often the head moves (tested: event count constant across repeated `integrate`).
- `integrate` with no id: `queue_empty`, `parked_without_new_evidence: {T: "challenger_not_merge_eligible"}`. `integrate T`: refused, exit 1, `reason_code challenger_not_merge_eligible`, message contains
  "owner promotion of the binding (registry change under champion-challenger-v1) is required"; branch head and store unchanged. `claim_integration` rechecks the queue head inside its transaction, so a race cannot bypass it.
- No waiver: `AGENTCTL_ALLOW_CANDIDATE_BINDINGS=1` does not lift it (tested end to end), and `integrate --allow-candidate-bindings` is a usage error (exit 2). No flag was added.
- Registry candidate `scope` text now says: routable only with explicit opt-in and only inside `sample_scope`; work by this binding is not merge-eligible until the owner promotes it. The five champions are byte-identical (one-line diff).
- The existing end-to-end tests in `test_execute.py` still assert the task reaches `integration_ready`; new ones assert `integrate` refuses it (also with the env var set), the integration branch is never created, and a champion-bound flow of the same shape integrates.

Mutate-and-confirm: `candidate_binding_roles` neutralised -> 14 failures/errors across `test_integrate_queue` and `CandidateBindingRunTests` (the governance blocker reproduced: pre-fix, explicit `integrate` merged the task);
`model_runs` source neutralised -> the 4 `reviewer_run`/`author_run` subtests fail. Failing-first: the queue tests were run with only the constants defined (11 failures/errors) before the implementation.

## FIX 2: candidate refused outside champion-challenger-v1 `sample_scope`

`routing.py`: `candidate_sample_scope()` reads `sample_scope` from `agent-system/routing/champion-challenger.json` (nothing hard-coded; a missing, empty or non-string-list scope is a `PlanningError`).
`route_capability`/`route_reviewer`/`route_task` pass the task risk; with the opt-in, a risk outside the scope **or not stated** returns the champion's refusal with an actionable message (risk, scope, re-class or owner promotion). Unknown risk fails closed:
existing tests that opted in without a risk now pass `risk="low"`. Tests: critical reviewer, high worker, low/medium still routed, scope edited in the policy fixture (narrowed and widened), malformed scope. Mutate (`risk not in scope` disabled): 4 failures. Failing-first: 24 of the 34 routing tests failed/errored before the change.

## FIX 3: malformed registry entry

`_registry_entries` validates every entry (`capability_class`, `provider`, `model_id`, `model_version`, `binding_type`: non-empty strings) and raises `PlanningError` naming entry index, class/model and the missing fields, with or without opt-in, for champions as well. Tests: each field missing/blank/non-string, loader-level context, a champion entry, the real registry valid.

## FIX 4: lifecycle test gap (no code change)

`test_attempt_evidence_for_an_attempt_other_than_the_latest_is_refused`: `attempt:<older>` is refused `evidence_wrong_attempt` with nothing recorded; `attempt:<latest>` is then accepted. The older attempt is set `failed` by a labelled **simulated** fixture mutation so the state check alone cannot be what refuses it.
Mutate (check disabled): the test fails (`LifecycleRefused not raised`, i.e. the older attempt's failure authorizes a repair of the newer one). `lifecycle.py` is unchanged.

## Coordinator add-on

`test_config_planted_by_a_gate_check_is_caught_and_stays_inert_afterwards` now carries `@mock.patch.dict(os.environ, {"AGENTCTL_CHECK_ISOLATION": "legacy-unsafe"})` (no assertion change). On this branch the variable is a no-op; isolation code was not touched.

## Residual and discovered dependencies

- **R1 (Lane A, `integrate.py`):** the `queue_empty` listing prints only the reason code for parked tasks; the remedy text appears in the explicit-`integrate <id>` refusal (done through `IntegrationRefused`, which this lane owns) but not in `parked_without_new_evidence`. `QueueEntry.parked_explanation` exists for a one-line change there. `integrate.py` was not edited.
- **R2:** a parked task never becomes eligible by waiting or by a registry edit (by design). After promotion it needs fresh review evidence from the promoted binding; no command re-reviews an `integration_ready` task, so in practice it is repaired/superseded. A typed owner re-qualification record is not implemented (AGENTS.md section 10 would require a sealed approval).
- **R3:** a `complex_implementation` task still cannot be reviewed (D5, report 1), so an author-candidate task cannot reach `integration_ready` through the real flow today; the author path is covered by the real producer (manifest marker and `model_runs`) plus simulated queue fixtures.
- **R4:** dependents of a parked task wait on it (`waiting_on`), as for any incomplete dependency.
- Detection of the verdict finding and the manifest marker depends on control-plane-written text; a champion reviewer that emitted the finding id would only park its own task (fails safe).
- Simulated: the provider (fake `claude`), and `Lab.tamper` for the stored-evidence cases in `test_integrate_queue.py`. Untested: any live provider.

## Files

Code: `agentctl/{routing,execute,integration_queue}.py`, `agent-system/routing/capability-registry.json` (candidate `scope` text only).
Tests: `tests/{test_routing,test_execute,test_integrate_queue,test_lifecycle}.py`.
