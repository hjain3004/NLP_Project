# Lane B report 3 -- repair for the second independent review (F1-F5)

**Branch:** `claude/lane-b-integration`, worktree `/home/user/wt/lane-b`. Base `dbba2c2`. No push.
**Attempt:** final allowed attempt for this finding (escalated capability class, AGENTS.md section 9).
The attempt was interrupted once by a tooling safety stop. The coordinator then re-scoped it:
no re-exec environment scrub, user+PID namespace as the primary F1(a) layer, synthetic sentinels
only in tests, and commits in green slices.

**Commits (slices):**

| Commit | Slice | Findings |
|---|---|---|
| `bc334d9` | 1-2: new `agentctl/gate_runner.py`, delegation from `integration_queue.run_gate_commands` | F2, F1(a), F5 |
| `a8dcd92` | 3: repository tamper check, driver neutralisation, late-driver refusal | F1(b), F1(c) |
| `e3e7586` | 4: lease fencing in transaction 2 and renewal in `_finalize`; safe tree materialisation | F3, F4 |
| `22332cd` | F5 completion: `agentctl integrate` drops credential variables from `os.environ` | F5 |
| (this report) | | |

**Write set respected.** Files touched: `agentctl/gate_runner.py` (new), `agentctl/integrate.py`,
`agentctl/integration_queue.py`, `agentctl/commands/integrate.py`, `tests/test_gate_runner.py`
(new), `tests/test_integrate_hardening.py`, `tests/test_integrate_recovery.py`, and this report.
`tests/helpers_lane_b.py` needed no change.

## Result

- Full suite (`PYTHONPATH=tools/agentctl python3 -m unittest discover -s tools/agentctl/tests -t tools/agentctl`)
  from `22332cd`: **304 tests, 1 error**. The error is the pre-existing `test_validate_valid` failure
  (`03-context-manifest.json`, the stale fixture pin fixed on another branch). It is unchanged.
  Report 2 had 278 tests and the same single error.
- Lane B suites (`test_gate_runner`, `test_integrate`, `test_integrate_queue`, `test_integrate_recovery`,
  `test_integrate_hardening`): **72 tests, OK**. They also pass from a **detached** worktree
  (`git worktree add --detach`, `## HEAD (no branch)`): 72, OK.
- After each run, no test process (`sleep 41.1xx`, `yes 41.104`) was left alive.

## Before-fix evidence

The regression tests were written first and run against the unchanged production code at
`dbba2c2`. Log: `/tmp/claude-0/lane-b-repair2-before.log`, 25 tests, 17 failures and 6 errors.
Two test-construction bugs were corrected and re-run, also against `dbba2c2`, and appended to the log.

**The log is redacted.** The first version of the F1(a) leak tests wrote the integrator's whole
environment to a file, and the assertion message printed it, so the log captured this container's
real environment variables. Those lines were replaced by a note that keeps only the synthetic
sentinel names. The tests were then changed so that a gate writes only `grep -c` counts of a
synthetic sentinel the test sets itself, plus file paths, never raw environment content.
A scan of the before log, the suite log, the mutation output and the tracked tree for the
container's credential markers found nothing.

## F1 (blocking) -- a gate reached and poisoned the integrator's repository

**Reproduced at dbba2c2:**
- `GateCannotReachIntegratorTests` (through the real `agentctl integrate` CLI): the gate's file contained
  `SECRET_TOKEN=s3cr3t-proc` and `CWD=<repository root>` (from `/proc/$PPID/environ` and `cwd`), plus the
  lock path under the repository from `/proc/$PPID/fd`.
- `GateCannotReachTheIntegratorProcessTests` (run_gate_commands in a separate process): the same leak.
- `GateRepositoryTamperTests`, the reviewer's PoC: T-A's gate planted `filter.x.smudge` and `* filter=x`.
  T-A was reported **integrated** (`(0, 'integrated') != (1, 'rejected')`). Planted hook and planted
  `refs/replace` gates were also integrated. A driver planted before the run **executed**
  (`True is not false : RAN`). A driver planted after startup was **not refused**.

**Fix, in layers:**
- **(a) Process reach.** `gate_runner.run_checks` probes once whether
  `unshare --user --map-root-user --pid --fork --mount-proc --kill-child` works, and caches the answer.
  When it does, each gate runs as a child of a small namespace init. The gate sees only its own PID
  namespace in `/proc`, so it cannot read the integrator's (or any outside process's) `environ`,
  `cwd` or fds. The evidence records `gate_isolation: "user_pid_namespace"`; on the fallback path
  it records `"none"`.
  - `integrator_hardening()` also marks the integrator non-dumpable and a child subreaper while gates
    run, and restores both afterwards.
  - Gates get an allow-listed environment, never `os.environ`. `agentctl integrate` drops
    `ANTHROPIC_*`, `CLAUDE*`, `*TOKEN*`, `*SECRET*`, `*KEY*`, `*PASSWORD*` and `*CREDENTIAL*` from its
    own `os.environ` at start.
  - The tests probe every visible process for the sentinel (`ANY=0`) and for the repository or
    runner path, not only `$PPID`.
- **(b) Tamper check.** Immediately before and after the gates, `_protected_state` hashes these parts of
  the real repository (`git rev-parse --git-common-dir`):
  - `config`, `config.worktree`, `HEAD`, `commondir`, `shallow`;
  - everything under `info/`, `hooks/`, `worktrees/` and `objects/info/` (alternates);
  - `refs/replace/*` and refs under the integration branch;
  - a hash of the effective configuration listing, which covers included files.

  The integrator's own detached worktree metadata is excluded. Any difference is rejected as
  `gate_tampered_repository` (`human_required`, `retry_requires_new_evidence`, changed names
  listed): no ref moves, nothing completes, and nothing is restored. The rejection records the
  hash of the tampered state. Every later run is refused with the same reason while the
  repository is still in exactly that state, so a human must inspect it. When the human changes
  it (for example by removing the planted keys), integration resumes.
- **(c) Drivers.** For every integrator git call, the integrator lists the code-running driver keys in
  repository configuration (`filter.<n>.clean|smudge|process`, `merge.<n>.driver`,
  `diff.<n>.command|textconv`; command scope ignored). It overrides each one through
  `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_n`/`GIT_CONFIG_VALUE_n`, which work for any driver name.
  - Filter commands become empty with `required=false`; this was verified empirically on git 2.43:
    the planted smudge did not run.
  - Merge drivers become `false`, so if one were ever reached the merge fails closed. The overlap
    rule means one should never be reached.
  - Diff drivers become empty.
  - The integrator also sets `GIT_NO_REPLACE_OBJECTS=1`, `gc.auto=0` and `maintenance.auto=false`.
  - A driver key that appears or changes after integrator startup refuses the integration
    (`gate_tampered_repository`) before `worktree add` and again before `merge`.

**After:** all F1 tests pass.
- The PoC gate's T-A is rejected with `gate_tampered_repository`, with `config` among the changed
  names. The planted key is still in `.git/config`. T-B is then refused with the same reason and the
  smudge log never exists. After the planted section and attributes are removed, T-B integrates, and
  the smudge never ran.
- A driver planted before the run is neutralised and T-B integrates unchanged. A driver planted after
  startup is refused. Hook and replace-ref planting are rejected.
- The probing gate sees `SENTINEL` and `ANY=0`, and neither the repository path nor the runner's cwd.

## F2 (high) -- marker-dropping descendants escaped the deadline

**Reproduced at dbba2c2:**
- `setsid env -u AGENTCTL_GATE_ID sleep … & exit 0` passed, and the sleep survived.
- `(setsid env -i sh -c 'sleep …' &); exit 0` behaved the same.
- A descendant of a timed-out shell survived.
- `head -c 100000000 /dev/zero; exit 0` passed with no size bound, and an orphaned `yes` kept running.

**Fix (`gate_runner.run_checks`):**
- Each gate runs in its own session and process group, with stdout and stderr going to files.
- The child is limited to `RLIMIT_FSIZE` 64 MiB, `RLIMIT_CORE` 0, and `RLIMIT_NPROC` set to this
  uid's current process count plus 512.
- After every gate (pass, fail or timeout) a sweep runs:
  - It finds this gate's descendants by walking parent-pid chains in `/proc/*/stat`.
    `/proc/<pid>/task/*/children` is unavailable on this kernel.
  - Roots are the unreaped shell and any new direct child. Orphans reparent here because the
    integrator is a subreaper. Processes carrying the gate marker are included too.
  - Children that existed before the gate are never touched, and a reaped gate pid is never reused
    as a root.
  - It counts first, then kills the group and every descendant, reaps zombies, and repeats until
    none remain, for at most 2 s.
- In namespace mode, the namespace init also reports how many non-zombie processes are still alive
  when the gate's shell exits, and everything dies with the namespace.
- Anything alive at shell exit means `reason: "gate_left_processes"`, a **failure** even after exit 0.
  Reaching the size cap means `output_limit_exceeded`. Results carry `left_processes`,
  `descendants_cleared`, `stdout_bytes`/`stderr_bytes` and `gate_isolation`.

**After:** `GateDescendantTests` and `GateDescendantFallbackTests` (the same cases with `isolation: "none"`,
so the subreaper and parent-pid walk do the work) all pass:
- the marker-dropping and double-forked descendants are killed and the gate fails, in under 5 s;
- the timed-out shell's descendant is killed at the deadline;
- output is bounded to 64 MiB or less and the gate fails;
- a gate that waits for its own children (`sleep 0.2 & wait`) still passes.

**Behaviour change:** report 2's `GateDeadlineTests` asserted `timed_out=True` for `setsid sleep 15 & exit 0`.
That gate now fails immediately with `gate_left_processes`, which is stricter, so the assertion
changed accordingly. The deadline for a hanging shell is still asserted by
`test_descendants_of_a_timed_out_gate_are_killed_at_the_deadline`.

## F3 (medium) -- expired lease completed with its old fencing token

**Reproduced at dbba2c2** (`test_lease_that_expired_after_the_ref_update_is_renewed_before_completion`, the reviewer's
`poc_lease`): after a kill at `after_ref_update` and an expired lease, the rerun completed with the
same lease id. `complete_integration` also accepted an expired lease and a manifest with a foreign
token or epoch.

**Fix:**
- `_finalize`, which the reconcile path uses, renews an expired lease (new epoch, new fencing token,
  `integration_lease_renewed`, old lease retired) before transaction 2.
- Inside its transaction, `complete_integration` requires the manifest's lease id, epoch, fencing
  token and integration id to equal the current integrable `integration-lease` artifact
  (`lease_fencing_mismatch`), and that lease to be live (`lease_expired`).

**After:** the three F3 tests pass. The record carries epoch 2, a new id and a new token; there is one
renewal event and no second merge. The expired lease and stale token/epoch are refused, and the task
stays `integrating` with no record.

## F4 (low) -- materialisation created directories outside the gate tree

**Reproduced at dbba2c2:** a crafted tree (`d -> OUT`, `d/deep/x`) created `OUT/deep`, and
duplicate or clashing entries raised a raw `FileExistsError`.

**Fix:** the whole listing is checked first for duplicates and file/directory clashes. Components
are then created one at a time relative to directory descriptors opened with `O_NOFOLLOW`, so no
ancestor can be a symlink. Every `OSError` maps to `candidate_tree_unsafe`, which is recorded as a
rejection on the integration path.

**After:** both tests pass. `OUT` stays empty, and duplicates and clashes are rejected as `candidate_tree_unsafe`.

## F5 (design) -- `gate_runner.py`

The public API is `run_checks(commands, cwd, timeout, env_extra=None, hard_limits=None)`,
`integrator_hardening()`, `scrubbed_environment()`, `namespace_isolation_available()` and
`drop_sensitive_environment()`. `integration_queue.run_gate_commands` (unchanged signature)
delegates to `run_checks`. The module has no integration dependency, so Lane A can adopt it for
`Scheduler.verify_candidate`. The module docstring states the residual risk set out below.

## Mutate-and-confirm (production line broken in this worktree, named tests run, restored with `git checkout`)

| Defence | Mutation | Result |
|---|---|---|
| namespace isolation | `isolate = False` | caught (CLI leak test fails; the in-process probe test reports `none` and skips) |
| descendant kill | kill loop over no pids | caught (fallback survivors) |
| subreaper | not set | caught |
| left processes = failure | `elif False` | caught (both modes) |
| namespace init report | ignored | caught |
| RLIMIT_FSIZE | not set | caught |
| tamper check | `if changed` -> `if False` | caught (3 tests) |
| hooks snapshot | `hooks` dropped | caught |
| refs snapshot | refs not recorded | caught |
| persistent tamper refusal | call removed | caught (PoC: T-B not refused) |
| driver neutralisation | env override removed | caught (planted smudge ran) |
| late-driver guard | `if new` -> `if False` | caught |
| `_finalize` renewal | `if False` | caught |
| tx-2 fencing | token comparison removed | caught |
| tx-2 liveness | `if False` | caught |
| F4 clash pre-check alone | removed | **not caught**: `O_EXCL` + `OSError` mapping still rejects (independent layer) |
| F4 `OSError` mapping alone | removed | **not caught**: the pre-check rejects first (independent layer) |
| F4 clash pre-check + `OSError` mapping | both removed | caught (raw `FileExistsError`) |
| F4 ancestor pre-check alone | removed | **not caught**: `O_NOFOLLOW` still refuses the symlinked ancestor |
| F4 `O_NOFOLLOW` alone | removed | **not caught**: the ancestor pre-check rejects first |
| F4 ancestor pre-check + `O_NOFOLLOW` | both removed | caught (`OUT/deep` created) |

The four uncaught rows are deliberately redundant pairs, and each pair's combined removal is caught.
Output: `/tmp/claude-0/lane-b-repair2/mut.out`.

## Residual risk (stated plainly; there is no OS sandbox)

1. **Gates run as the integrator's uid.** The namespaces hide processes, not files. A gate can still
   read or write anything that uid can, by absolute path. That includes the repository, which the
   tamper check detects afterwards, and **the event store, which nothing checks**.
   - The store was deliberately not snapshotted: other Lane A processes may write it legitimately
     during an integration, and a fail-closed check would park the queue.
   - Repository objects can be deleted, an integrity problem that is not checked either.
   - This is the AGENTS.md section 7 `unsandboxed_tools` class: postcondition checks only, globally
     serialized, secret-free, and forbidden for high-risk tasks.
2. **Without `unshare` (`gate_isolation: "none"`), F1(a) is not closed for a root integrator.**
   `PR_SET_DUMPABLE=0` does not stop a same-uid process holding `CAP_SYS_PTRACE`, and the mutation
   above demonstrates it. For a non-root integrator, being non-dumpable blocks those reads, but a
   gate can still read the `/proc` entries of other same-uid processes, such as the integrator's
   parent shell. The evidence records `"none"` honestly.
3. **`--map-root-user`** means the gate sees itself as uid 0 inside its namespace. This is a visible
   semantic difference, recorded as `gate_isolation`.
4. **`drop_sensitive_environment`** does not rewrite the kernel's initial environment block
   (`/proc/<pid>/environ`). Under the namespace no gate can see it. Without the namespace, a
   same-uid privileged reader still can.
5. **The tamper check is a before/after comparison.** A gate that changes metadata and restores it
   before exiting is not detected. Driver neutralisation still prevents planted drivers from running
   in the integrator's own git, but other metadata could still take effect during that window.
   Concurrent legitimate writers to `.git` metadata during the gates would cause a fail-closed
   rejection, which is consistent with serialization.
6. **Other limits.** `RLIMIT_NPROC` is not enforced by Linux for uid 0. Outside a namespace, a fork
   storm can outrun the 2 s bounded sweep; this is recorded as `descendants_cleared: false`, which
   counts as a failure. Escape through another daemon (cron, `at`, a socket to a different service)
   is out of reach. Descendant tracking assumes no other thread of the integrator starts children
   while a gate runs.
7. **Scope of the tamper evidence.** In namespace mode, the namespace init's report of left
   processes can be forged by the gate, because it is written to a scratch file the gate can see.
   The processes die with the namespace regardless; only the `gate_left_processes` record could be
   suppressed.

## Demonstrated / simulated / untested

- **Demonstrated** (real git 2.43, real event store, real processes, Linux 6.18, root, user+PID
  namespaces available): every "After" above, the full suite, and the detached-worktree run.
- **Simulated:** hostile gates are real commands written by the tests. Lease expiry and crash
  instants are simulated as in report 2 (`Lab.tamper`, `os._exit`). The late-driver test plants the
  driver between `_Git` construction and `_plan`.
- **Untested:** a non-root integrator, both with and without namespaces (the fallback was tested as
  root only); hosts without `unshare`; non-Linux platforms (the guards exist but were not exercised);
  merge-driver neutralisation on a real conflicting path, which the overlap rule prevents.

## Discovered dependencies

1. **Lane A, `Scheduler.verify_candidate`.** It runs contract checks in the candidate's worktree
   with an inherited environment and has the same exposure. It can adopt `gate_runner.run_checks`
   directly, run on an `.git`-less copy outside the checkout, and reuse the `_Git` hardening
   (driver neutralisation, `GIT_NO_REPLACE_OBJECTS`).
2. **Event-store integrity against gates.** This is residual 1 above. It needs a design decision,
   for example a store outside the gate uid's reach, or a separate gate uid, which an OS-level
   sandbox would provide.
3. Report 1 and 2 dependencies stand unchanged.

## Schema proposals

None. New reason codes (`gate_tampered_repository`, `lease_fencing_mismatch`, `lease_expired`) and
the new gate-result fields are carried in existing payloads and details. The manifest's
`affected_gates_rerun` still records only `gate` and `result`.
