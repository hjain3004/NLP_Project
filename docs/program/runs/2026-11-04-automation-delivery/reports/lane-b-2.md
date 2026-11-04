# Lane B report 2 -- repair cycle for the independent review (2 blocking, 5 non-blocking)

**Branch:** `claude/lane-b-integration`. **Repair commit:** `727fd2c` (on top of `9d51a9e`, report 1).
**Write set:** unchanged. Files touched: `agentctl/integrate.py`, `agentctl/integration_queue.py`,
`tests/helpers_lane_b.py`, `tests/test_integrate.py`, `tests/test_integrate_recovery.py`, new
`tests/test_integrate_hardening.py`, and this report. Nothing outside the write set. No push.

## Result

- Full suite: `cd /home/user/wt/lane-b && PYTHONPATH=tools/agentctl python3 -m unittest discover -s tools/agentctl/tests -t tools/agentctl`
  -> **278 tests, 1 error**, the pre-existing `test_validate_valid` (`03-context-manifest.json`, stale `AGENTS.md` hash). Not touched.
- Lane B tests: 45 (was 33). Run from a **detached** worktree as well (`git worktree add --detach`): the Lane B suites pass (item 7).
- For every defence below I broke the production line and confirmed the named test fails, then restored it (mutate-and-confirm table at the end).

## Blocking 1 -- gate escapes the env scrub and poisons later integrator runs

**Reproduced first, as failing tests, against `9d51a9e`** (`tests/test_integrate_hardening.py`, log `/tmp/before-fix.log`):

| Test | Before the fix |
|---|---|
| `test_gate_has_no_git_link_and_cannot_change_repository_configuration` | FAIL: `AssertionError: True is not false : LINKED` -- the gate's working directory had a `.git` link and was inside the checkout (and `git config` from it wrote to the shared repository config) |
| `test_planted_git_configuration_cannot_run_code_in_later_integrator_runs` | FAIL: second integration returned `rejected / candidate_binding_mismatch` (`actual_diff` = hash of empty output: the planted `diff.external` replaced `git diff`'s output), exactly the reviewer's observation |

**Fix (two layers):**
1. *Containment.* Affected gates no longer run in a `git worktree`. After the merge is planned and its tree identity read from the detached worktree, the planned commit is written byte for byte (`git ls-tree` + `git cat-file --batch`) into a fresh `tempfile.mkdtemp` directory outside the repository: no `.git`, no attributes consulted (so a candidate's `export-ignore` cannot hide files from its own gates, which `git archive` would allow), paths validated (a tree carrying `.git/...`, `..` or absolute components is refused as `candidate_tree_unsafe`; submodule links skipped). The gate environment additionally sets `GIT_CEILING_DIRECTORIES=/` and a throwaway HOME.
2. *Hardened integrator git.* Every integrator git call is built as `git --no-pager -c core.fsmonitor=false -c core.pager=cat -c diff.external= -c core.hooksPath=/dev/null -c core.attributesFile=/dev/null -c commit.gpgsign=false ...`, runs with an allow-listed environment (`PATH`, throwaway `HOME`, `LC_ALL`, `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_TERMINAL_PROMPT=0`, `GIT_OPTIONAL_LOCKS=0`; no inherited `GIT_*`), and diff-style calls pass `--no-ext-diff --no-textconv`. Command-line `-c` outranks repository config, so a planted `diff.external` or `core.fsmonitor` is inert even if some other component plants it.

**After:** both tests pass. The containment test asserts the repository's `.git/config` bytes are identical before and after the gate ran. The second test has the gate plant `diff.external` **and** `core.fsmonitor` by absolute path, with `SECRET_TOKEN` in the integrator's environment; the next candidate integrates, and the planted command never runs (log file absent). Mutation: dropping `containment` (`cwd=worktree`) fails the first test; dropping both `-c diff.external=` and `--no-ext-diff` fails the second; dropping only one of them leaves it passing (the layers are independent).

**Residual risk, stated plainly:** there is still no sandbox. A gate runs as the integrator's uid and can write anywhere that uid can, for example to the repository or the event store by absolute path if it can find them. What is closed: the working-directory route the reviewer used, and execution of planted *git configuration* by the integrator's own git. What is not closed: git `filter.<name>.clean|smudge|process` and `merge.<name>.driver` drivers planted in repository config combined with an in-tree `.gitattributes` would still run during `git worktree add` / `git merge` checkouts (they have no global off switch and `-c` cannot name drivers in advance). Planting still requires a process that can write `.git/config`; the candidate's own `scheduler.verify_candidate` worktree run (Lane A's code) is such a process -- see dependency 1.

## Blocking 2 -- gate timeout not enforced

**Reproduced first:** `test_descendant_that_escapes_the_process_group_cannot_extend_the_timeout` against `9d51a9e`: FAIL `AssertionError: 15.004... not less than 5.0 : gate deadline not enforced: 15.0s` (`run_gate_commands(['setsid sleep 15 & exit 0'], timeout=1)` returned after 15 s).

**Fix** (`run_gate_commands`): stdout/stderr go to files in a scratch directory, never pipes, so no descendant can hold the parent's pipe; output hashes are computed by streaming the files. One hard deadline per command covers the shell *and* any descendant that outlives it: each gate carries an `AGENTCTL_GATE_ID` marker in its environment, and after the shell exits the runner scans `/proc/*/environ` for survivors (this finds `setsid`/daemonised descendants that left the process group). On expiry it `killpg`s the group (the shell is its own session leader) and `SIGKILL`s every marked straggler, repeating for up to 2 s to catch re-forks, and records `timed_out: true, exit_code: null, result: fail`. After the deadline it never waits on the child unboundedly. The lock file descriptor is not inherited (`O_CLOEXEC`), so a hung gate cannot hold the integration lock past the deadline.

**After** (measured, timeout 1 s): `setsid sleep 15 & exit 0` -> **1.05 s**, `fail`, `timed_out=True`, and no `sleep` process left running; `sleep 60` -> 1.00 s, timed-out failure; `echo ok` -> 0.00 s, pass. Mutation: disabling the straggler wait makes the test fail.
**Limit:** the straggler search uses Linux `/proc`; on other platforms only the process-group kill applies (a `setsid` escape would then not be found, but cannot block the parent, which uses no pipes).

## Non-blocking

3. **Merge attributes can silently resolve a conflict.** On the `--no-ff` path, before any worktree is created, the integrator computes the paths the candidate changed since the merge base and the paths the integration head changed since the merge base; any overlap is `conflict_repair_required` (this is the reviewer's "head blob at the path must equal the merge-base blob" rule, and is deliberately stricter than git: two edits to different hunks of one file are also rejected). After the merge, the planned tree must equal the candidate on every candidate-touched path, or `candidate_altered`. `core.attributesFile` is neutralised; in-tree attributes are made irrelevant by the overlap rule. Test: a real `.gitattributes` `merge=union` conflict where a precondition assertion proves plain `git merge` would have merged both edits cleanly; the integrator rejects it with the branch untouched.
4. **Lease expiry is effective.** `_resume` mints a new lease (epoch+1, new fencing token, recovering run id) when the recorded one has expired, via `renew_lease` (event `integration_lease_renewed`, old lease retired in the same transaction). `complete_integration` now verifies inside the transaction that the intent and the manifest's lease artifacts are still `integrable`, else `intent_not_integrable` / `lease_not_integrable`. The manifest carries the *current* lease and the recovering run id as `integrator_session_id`. Tests: expiry -> new epoch/token/id in the record and one renewal event; completion refused with a retired intent.
5. **Ids that become paths** go through `_safe_component` (task-id pattern, no `..`) in `_integration_dir`. Test: `../escape`, `a/b`, empty, `.hidden`, `x y`, `a..b` refused.
6. **Tests for the previously unexercised defences** (each mutated and confirmed): reconcile via first-parent chain after later commits sit on the planned merge; branch moved between planning and claim (task stays `integration_ready`, nothing claimed); `candidate_altered` (simulated tampering `git` wrapper that rewrites the merge commit); replay plan-equality (recorded `planned_at` perturbed so the replay differs from the recorded commit).
7. **Helper robustness.** `Lab` now creates and checks out its own named branch in the clone instead of reading `symbolic-ref` from the source checkout, so it works when the suite is run from a detached worktree.

## Mutate-and-confirm (production line broken, test must fail; then restored)

| Defence | Mutation | Failing test |
|---|---|---|
| first-parent reconcile | `head == planned or planned in chain` -> `head == planned` | `..._recognised_by_first_parent_chain` |
| claim branch check | `if actual_head != branch_head_before` -> `if False` | `test_branch_moved_between_planning_and_claim...` |
| preserved / candidate_altered | `if not preserved` -> `if False` | `CandidateAlteredTests` |
| replay plan equality | `if plan.commit != planned_commit` -> `if False` | `test_replay_that_would_not_reproduce...` |
| lease renewal | `if not lease_is_live(lease)` -> `if False` | `test_expired_integration_lease_is_replaced...` |
| integrable checks in tx 2 | `if live is None or live[...] != 1` -> `if False` | `test_completion_is_refused_if_the_intent...` |
| overlap rule | `if overlap` -> `if False` | `MergeAttributeTests` (union) |
| id validation | drop `_safe_component` | `TaskIdPathTests` |
| gate deadline | straggler wait -> `while False` | `GateDeadlineTests` |
| gate containment | `cwd=gate_tree` -> `cwd=worktree` | `test_gate_has_no_git_link...` |
| `.git` path refusal | drop `part.lower() == ".git"` | `test_crafted_tree_with_a_dot_git_entry...` |
| hardened diff | drop `-c diff.external=` and `--no-ext-diff` | `test_planted_git_configuration...` |

(Report-1 mutations -- single-slot check, candidate worktree HEAD, `reconcile()`, gate failure, env scrub, compare-and-swap -- still pass their tests after this change.)

## Demonstrated, simulated, untested (delta from report 1)

- **Demonstrated** (real git, real event store, real processes): blocker 1 and 2 reproductions and fixes above; union-attribute conflict rejected; gate tree is the exact planned tree (`export-ignore` file present, no `.git`); crafted `.git` tree refused.
- **Simulated:** the merge-time tampering wrapper (`CandidateAlteredTests`); time passing for lease expiry and a perturbed `planned_at` (SQL via `Lab.tamper`); the gate that plants configuration (a real shell command run by the real runner, but written by the test to model a hostile gate).
- **Untested:** `filter`/`merge` driver planting (residual above); non-Linux straggler detection; very large trees through `_materialize_tree` (it streams `cat-file --batch` but holds each blob in memory); gates that legitimately need git history (they no longer have a repository -- a contract whose `deterministic_checks` call `git` will now fail at integration and should be reported by the planner, not worked around here).

## Discovered dependencies for Lane A (additions to report 1)

1. **`Scheduler._git` and `verify_candidate` have the same exposure.** `Scheduler._git` runs plain `git` (default config, inherited environment) for admission, ingest provenance and the diff hash, and `verify_candidate` runs contract checks inside the candidate's `git worktree` (shared `.git`, inside the checkout). That is the process that can plant configuration, and the code that would execute it on the next `dispatch`/`ingest`/`verify`. Request: apply the same command-line hardening and allow-listed environment to `Scheduler._git` (the exact flag list is `_Git._HARDENING` and `_Git._env` in `integrate.py`), add `--no-ext-diff --no-textconv` to its `diff` calls, and run candidate checks on an `.git`-less copy outside the checkout with the same timeout/deadline runner. `integration_queue.run_gate_commands` is directly reusable (it is a module-level function with no integration dependency); if Lane A prefers not to import across lanes, copy it.
2. Items 1-5 of report 1 (missing `integration_ready`/`integrating` transitions, `integration_order` not carried by `TaskSpec`, unused `integrations` table, ...) stand unchanged.

## Schema proposals

None.
