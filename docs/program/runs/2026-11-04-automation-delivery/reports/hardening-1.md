# Hardening report 1 -- vacuous verification, unsandboxed high-risk gates, `verify` credentials

**Branch:** `claude/verify-hardening` from `182e4f1`. **Commits:** `3d60647` (fixes and regression tests),
`a42f5a9` (one fixture edit), plus this report. No push, no schema or registration change.

## Fixes

| Id | Gap | Fix |
|---|---|---|
| F3 | `all([])` made a contract with no deterministic checks a passing verification | `Scheduler.verify_candidate`: no checks gives `passed: false`, evidence `failure_reason: no_deterministic_checks`, event `implementation_defect` with reason `no_deterministic_checks`, state `repair_required`, evidence not integrable. `integrate._verify` rejects such a task (`repair_required`, reason `no_deterministic_checks`, parked until new evidence) before any other check. |
| F1a | `agentctl verify` did not call `drop_sensitive_environment` | `commands/verify.py` calls it first in `run`, as `commands/integrate.py` does. |
| F1b | Gates run unsandboxed at the control-plane uid, yet nothing refused `high`/`critical` tasks (AGENTS.md section 7) | `verify_candidate` raises `PermissionViolation` (`reason_code=unsandboxed_gate_high_risk`) before any state transition, event, artifact or check. `integrate._verify` records a non-advancing `human_required` rejection with the same reason, before planning or any gate. `medium` and `low` are unchanged. |

Production lines changed: `scheduler.py` (verification section plus three module constants), `integrate.py`
(one import and one pre-claim block at the top of `_verify`), `commands/verify.py`. The integrate refusals
reuse the existing `_reject` / `record_rejection` path (`retry_requires_new_evidence=True`, so a permanently
refused task is parked and cannot wedge the queue).

## Regression tests (`tests/test_verify_hardening.py`, 6 tests)

Each was run against the pre-fix code first and failed for the stated reason, then passed after the fix.

| Test | Pre-fix failure |
|---|---|
| `test_verification_of_a_contract_with_no_checks_fails_with_a_named_reason` | `'independent_review' != 'repair_required'` |
| `test_integration_rejects_a_task_whose_contract_has_no_checks_and_moves_nothing` (SIMULATED legacy state) | `'integrated' != 'rejected'` |
| `test_verify_refuses_high_and_critical_tasks_before_any_transition_or_check` (2 subtests) | `PermissionViolation not raised`, for both risks |
| `test_verify_still_verifies_a_medium_risk_task` (boundary) | passes before and after, by design |
| `test_integration_rejects_a_high_risk_task_without_running_gates_or_moving_the_branch` (SIMULATED) | `'integrated' != 'rejected'` |
| `test_verify_command_drops_credential_like_variables_from_its_environment` | sentinel variables still present in the verify process |

## Mutation confirmation (production line broken, test must fail, then restored from a byte-exact backup)

| Guard | Mutation | Result |
|---|---|---|
| verify vacuous | `vacuous = False` | no-checks verify test FAILS |
| verify risk | `if False:` | high/critical test FAILS (both subtests) |
| verify credentials | `drop_sensitive_environment()` -> `pass` | environment test FAILS |
| integrate risk | `if False:` | integrate high-risk test FAILS |
| integrate no-checks | `if False:` | integrate no-checks test FAILS |

## Limits stated plainly

- **F1a observable.** Gates never inherit `os.environ` (they get an allow-listed environment), and the kernel's
  `/proc/<pid>/environ` is not rewritten by `os.environ` deletion. A probe inside a check therefore cannot tell
  whether `verify` dropped its credentials. The test runs the real CLI (`agentctl.cli.main(['verify', ...])`) as a
  subprocess with synthetic sentinels (`ANTHROPIC_API_KEY`, `SYNTHETIC_AUTH_TOKEN`, `CLAUDE_SYNTHETIC_SESSION`)
  and a synthetic non-sensitive control, and records, at the moment the command hands off to `verify_task`,
  whether each name is in `os.environ` (names only, never values). The probe wrapper replaces `verify_task` to
  observe; the real command, dispatcher and verification still run.
- **Simulated.** The two integrate tests corrupt stored task state through `Lab.tamper` (empty checks; risk
  `high`) to stand in for evidence an older build could have stored as a pass. They prove integration does not
  trust a stored pass over the contract, not that the corruption occurs through ordinary commands.
- **Untested.** No live provider was involved. A `high`/`critical` task under a genuinely sandboxed enforcement
  class is still refused by `verify`/`integrate` (the instruction was to key on contract risk alone); a future
  sandboxed gate runner would need to relax this deliberately.

## Existing-test edits (all additive; no assertion changed, none weakened)

Each relied on vacuous verification (a `TaskSpec` or helper default with no checks), which now correctly lands in
`repair_required`:

1. `tests/helpers_lane_b.py` -- `Lab.candidate` default `checks=()` -> `("true",)`. 40 Lane B tests built candidates through it.
2. `tests/test_security_hardening.py` -- the `TaskSpec` in `TrustedCandidateTests.setUp` gains `deterministic_checks=("true",)` (2 tests).
3. `tests/test_review_independence.py` -- the `TaskSpec` in its candidate builder gains `deterministic_checks=("true",)` (10 errors in the first full run).

## Results

Named branch `claude/verify-hardening`, `PYTHONPATH=tools/agentctl python3 -m unittest discover -s tools/agentctl/tests -t tools/agentctl`:

- First full run (after commit `3d60647`): 410 tests, 10 errors, all in `test_review_independence` (edit 3 above).
- After `a42f5a9`: **410 tests (404 + 6 new), OK, 1 skipped (live-provider test), 0 failures, 0 errors** (241 s).

## Review repair (independent review: one blocking, one non-blocking finding)

**Blocking: blank checks still verified.** The schema allows `deterministic_checks: [""]` and `/bin/sh -c ""`
exits 0, so `("",)` and `("   ",)` verified and integrated as a pass. Fix: `scheduler.effective_checks` keeps only
non-blank `str` entries; `verify_candidate` and `integrate._verify` test `not effective_checks(...)` instead of
`not checks`.

- **Policy (decided):** blank or non-string entries are *ignorable next to a real check* and are neither run nor
  recorded (verification evidence and the integration gate list carry only the real checks); a contract whose
  entries are *all* blank is vacuous, identical to an empty list (`no_deterministic_checks`).
- **Schema not changed.** Proposal for a human decision (schema commitment): add `"minLength": 1` and
  `"pattern": "\\S"` to the `deterministic_checks` items in `task-contract.schema.json` so a blank check is
  rejected at registration rather than at verification.

**Non-blocking, done: allow-list for risk.** `scheduler.unsandboxed_gate_refusal` normalises with
`str(risk).strip().lower()` and lets only `low` and `medium` proceed. `high`/`critical` (any case or padding) keep
reason `unsandboxed_gate_high_risk`; anything else, including a missing risk in integrate's stored payload, is
refused with `unsandboxed_gate_unknown_risk`. Both verify (`PermissionViolation`, before any transition) and
integrate (non-advancing `human_required` rejection) use the same function. `" Low"` and `"MEDIUM "` still verify.

**Tests added (7 test methods, `test_verify_hardening.py` now 13 tests), failing on the previous code:**
all-blank verification (`("",)`, `("   ",)`, `("\n\t", "")`), mixed blank+real verification, integrate blank-only
(SIMULATED stored state), integrate mixed blank+real, verify allow-list (`" High"`, `"CRITICAL"`, `"bogus"`, `""`),
integrate allow-list (`" High"`, `"bogus"`, risk missing; SIMULATED), plus the passing boundary test for `" Low"`/`"MEDIUM "`.
Against the previous code 13 subtest/test failures were observed (the boundary test passes before and after).

**Mutation confirmation (restored from backup after each):** verify uses raw checks (4 failures); integrate guard
uses raw list (1); integrate gates unfiltered (1); deny-list instead of allow-list (4); no normalisation (5).

No existing test was edited in this repair.
