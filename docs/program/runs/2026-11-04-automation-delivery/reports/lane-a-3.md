# Lane A report 3: verification through the shared gate runner

Merge of `claude/brave-keller-7gd2bc` into `claude/lane-a-routing`: `git merge --no-edit` completed with **no conflicts** (commit `3472a52`).
The merge also brought in the fixture-pin refresh, so the old baseline error (`test_validate_valid`, stale `AGENTS.md` hash) is gone.

## Result of the full suite: NOT green, 5 of 390 tests fail, all in Lane B's `test_integrate_hardening.py`

390 tests, 1 skipped (opt-in LIVE), 4 errors + 1 failure. I verified these 5 pass on the merged branch with the previous `scheduler.py` (16/16 in that file) and fail only
after this change. They are not regressions in behaviour; they encode an assumption my change removes. Everything of Lane A's, and the rest of Lane B's suite, passes.

Failing tests (Lane B files, not edited by me):
- `GateContainmentTests.test_gate_has_no_git_link_and_cannot_change_repository_configuration`
- `GateRepositoryTamperTests.test_gate_that_plants_a_hook_is_rejected`, `..._creates_a_replace_ref_is_rejected`, `..._plants_a_filter_driver_is_rejected_and_the_driver_never_runs`
  Cause: `helpers_lane_b.Lab.candidate()` registers the tampering command as a contract check and calls `verify_candidate`, expecting it to pass so that the integrator can later
  catch the tampering. `verify_candidate` now detects the same tampering in the leased worktree (the new requirement) and fails verification (`gate_tampered_repository`), so
  the task never reaches `independent_review` and `Lab.review` raises `IllegalTransition`.
- `GateCannotReachIntegratorTests.test_gate_cannot_read_the_integrator_environment_or_repository_path_through_proc`
  Cause: the gate only logs when `$HOME` matches `*agentctl-gate-home*`, which now also holds during verification. Verification legitimately runs with cwd = the leased worktree
  inside the repository, so the log contains the repository path from the verification run, not from the integration run the test is about.
Suggested Lane B-side fix (request to coordinator): make the tampering/leak gates act only in the integration tree (for example guard with `[ ! -e .git ]`, since integration gates run in a
`.git`-less copy and verification gates do not), or let `Lab.candidate` seed the verification evidence without running the tampering command. I did not touch Lane B files.

## Change (scheduler.py only, plus tests I own)

- `Scheduler.verify_candidate` runs the contract's checks through `gate_runner.run_checks` (allowlisted environment, rlimits, user+PID namespace where available, descendant
  sweep, `gate_left_processes`, hard deadline per check). On this host namespace isolation is available. The private runner (`_run_check`, `_check_environment`, pipe/temp-file
  code) is deleted. `PYTHONPATH`, when set, is passed through `env_extra`; nothing else is inherited. `CHECK_TIMEOUT_SECONDS` is still the per-check limit.
- Tamper audit: new `Scheduler._run_gate_checks` takes the integrator's protected-state snapshot (`integrate._Git`, `_protected_state`, `_changed`, `DEFAULT_BRANCH`) before and
  after the checks and treats any difference as failed verification with event reason `gate_tampered_repository`. The candidate's own worktree metadata directory
  is excluded (a check that merely runs `git status` refreshes it; covered by a test). If the baseline snapshot cannot be taken, no check runs and verification fails closed
  with `gate_tamper_check_unavailable`. Nothing is restored; the changed metadata stays for inspection. The helpers are imported lazily inside the method, reused rather than copied;
  a module-level import would be circular (`integrate` -> `integration_queue` -> `scheduler`). They are underscore-private in `integrate.py`, so I recommend the coordinator ask
  Lane B to expose them (or move them next to `gate_runner`).
- Evidence shape is backward compatible: `{"candidate_binding", "checks", "passed"}` is unchanged. Each check keeps `command`, `exit_code` (124 on timeout), `stdout_hash`,
  `stderr_hash`, and `timed_out` when true; it adds `reason`, `left_processes`, `gate_isolation`, `duration_ms`. A tampered run adds `repository_tamper`
  (`reason_code`, `changed` up to 50 entries, `state_after`). A check with exit 0 that left processes behind is a failed check (`reason: gate_left_processes`), so `passed` is
  false even though `exit_code` is 0.

## Regression tests (written first, run against the old runner, then the new one)

| Test (`test_scheduler.VerifyCandidateHardeningTests`) | Before | After |
|---|---|---|
| `test_a_check_planting_git_config_in_the_shared_repository_fails_verification` | FAIL (verification passed) | pass |
| `test_a_setsid_straggler_fails_verification_and_does_not_survive_it` (marker file written 4 s after the check exits is never created; verdict `gate_left_processes`) | FAIL | pass |
| `test_the_candidates_own_worktree_bookkeeping_is_not_tampering` (guards against false positives) | pass | pass |
| `test_a_hanging_check_is_a_failing_check_and_nothing_it_started_survives` (rewritten from pid-file to marker-file, because inside the namespace `$!` is not a host pid) | pass | pass |
| `test_checks_do_not_see_provider_credentials_or_the_operator_home` (unchanged) | pass | pass |

`test_execute.GitHardeningTests` was adjusted: a check that plants git config now fails verification, so `review run` is no longer reachable in that test; it now asserts the
failure and that the planted config stays inert for subsequent hardened control-plane git calls.

## Residual risks

- A verification gate still runs in the candidate's worktree, which shares `.git` with the repository. The tamper audit detects changes after the fact (and nothing is rolled back);
  it does not prevent them. It also compares shared state, so another worktree created or removed by a concurrent `agentctl run` during a check can cause a false
  `gate_tampered_repository` (a safe-side failure, retried by re-verification). Running verification on a `.git`-less copy, as the integrator does, would remove both problems but contract checks such as `git diff`
  need the link; that is a design decision.
- `gate_runner` is not an OS sandbox (its own stated residual risk applies unchanged).
- A tampered verification currently goes to `repair_required` like any failed check, not `escalated`; `repair_required` still has no authorize path.
