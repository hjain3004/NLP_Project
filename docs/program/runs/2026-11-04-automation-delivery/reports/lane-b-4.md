# Lane B report 4 -- align Lane B tests with Lane A's verification-time tamper audit

**Branch:** `claude/lane-b-integration`. **Commit:** `14f8b9e` (tests) plus this report.
**Scope:** Lab/test files only (`tests/test_integrate_hardening.py`). No edit to `scheduler.py`,
`gate_runner.py` or any production file. No push.

## What happened

`git merge --no-edit claude/lane-a-routing` completed as a **fast-forward with no conflicts** (the branch
already contained Lane B's history; it brought Lane A's `gate_runner.py`, `verify_candidate` through the shared
runner, and `3e3792c`'s tamper audit). Before touching any test I ran `tests.test_integrate_hardening`:
**16 tests, 1 failure + 4 errors**, exactly the five the coordinator listed:

| Test | Why it failed |
|---|---|
| `GateContainmentTests.test_gate_has_no_git_link_and_cannot_change_repository_configuration` | error: the probe ran `git config diff.external ...` inside the leased worktree during *verification*; Lane A's audit failed verification, so the candidate never reached `independent_review` and `Lab.candidate()` raised |
| `GateRepositoryTamperTests` hook / replace-ref / filter-driver (3 tests) | same: the planting command was a contract check, guarded only by `$HOME` matching `agentctl-gate-home`, which verification now shares with integration (same runner, same HOME) |
| `GateCannotReachIntegratorTests.test_gate_cannot_read_the_integrator_environment_or_repository_path_through_proc` | the `$HOME` guard fired at verification too, so the log was written by the wrong run |

## Change

The planting/leak commands now act only when the **working directory is the integration copy**:
`case "$PWD" in *agentctl-integration-gate-*)`. That copy is the one place with no `.git` entry (verification runs
in the leased worktree, which has a `.git` link file). I used the working directory rather than the suggested
`[ ! -e .git ]` alone because the containment test's whole job is to *detect* a `.git` in the integration copy; a
`.git`-absence guard would make that probe unreachable.

- `GateRepositoryTamperTests.gate()` and the `/proc` leak test: guard changed from `$HOME` to the integration-copy `$PWD`. Assertions untouched.
- `GateContainmentTests` containment test: same guard, **plus a positive control** (the gate also writes one `COPY` line, and the test asserts it ran exactly once in the integration copy).
- **New:** `test_tampering_at_verification_time_never_reaches_integration` -- through the public flow, the same planting command run at VERIFICATION (guarded by `[ -e .git ]`, i.e. in the leased worktree) makes `Lab.candidate(..., review=False)` end in `repair_required`; the stored verification evidence has `passed: false` and `repository_tamper.reason_code == gate_tampered_repository` naming `hooks/post-checkout`; after a human removes the hook, `integrate` refuses with `task_not_integration_ready`, the branch is untouched and there is no record. The audit is Lane A's; this asserts the end-to-end effect only.

## A gap I found in my own guard (and closed)

Re-verifying by mutation showed that with the integration gate moved back into the worktree (`cwd=worktree`), the
containment test **still passed**: the new `$PWD` guard meant the probe never fired, so it passed vacuously. The
positive control above fixes that.

## Re-verification by mutation (production line broken, test must fail, then restored from a byte-exact backup)

| Defence | Mutation | Result |
|---|---|---|
| integration-time tamper detection (`integrate._plan`) | `if changed:` -> `if False:` | filter-driver, hook and replace-ref tests all FAIL |
| containment | gate `cwd=gate_tree` -> `cwd=worktree` | containment test FAILS (needed the positive control) and the `/proc` test FAILS |
| containment | write a `.git` file into the integration copy | containment test FAILS (`LINKED`) |
| verification-time audit (Lane A, `scheduler._run_gate_checks`, temporary) | `if not changed:` -> `if True:` | the new verification-time test FAILS |
| `/proc` isolation (Lane A, `gate_runner.namespace_isolation_available`, temporary) | force `False` | the `/proc` leak test FAILS |

The two Lane A lines were mutated only transiently to confirm the Lane B tests exercise them; both files are unchanged in the commit (`git status` clean after each).

## Results

| Run | Command | Result |
|---|---|---|
| Named branch `claude/lane-b-integration` | `cd /home/user/wt/lane-b && PYTHONPATH=tools/agentctl python3 -m unittest discover -s tools/agentctl/tests -t tools/agentctl` | **391 tests, OK, 1 skipped** (191 s) |
| Detached worktree (`git worktree add --detach`, `git symbolic-ref -q HEAD` fails) | same command from that worktree | **391 tests, OK, 1 skipped** (197 s) |

- The skip is Lane A's live-provider test (`test_claude_cli_adapter.py`, needs `AGENTCTL_LIVE_PROVIDER=1`); no Lane B test is skipped on this host. Two Lane B tests carry platform guards (`/proc` is Linux-specific; `setsid` needs util-linux) and both ran.
- The full suite no longer shows the earlier `test_validate_valid` baseline error: the merged branches carry the fixture-pin refresh, so there are **0 errors, 0 failures**. I did not touch that fixture.
- Lane B tests in `test_integrate*.py`: 47 (was 46: +1 new test).

## Untested or unchanged

- Nothing in this task changes production behaviour, so the earlier reports' untested list stands (`--base`, `record_git_divergence`, `lease_expired`, non-Linux straggler detection, real provider-authored candidates).
- The `/proc` test passing depends on `unshare --user --pid` working on the host; where it does not, Lane A's runner records `gate_isolation: "none"` and says so, and the test would then fail rather than skip. That is deliberate (a skip would hide a loss of isolation) but is a portability note for CI.
