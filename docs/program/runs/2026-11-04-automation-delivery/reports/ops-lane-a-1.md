# Ops lane A, report 1 -- enforced candidate-check boundary, one check-environment contract, lease fencing, late-manifest orphaning

**Branch:** `claude/ops-isolation` (worktree `/home/user/wt/iso`), base `81063b1`. No push, no history rewrite.
**Commits:** `60308b7` sandbox; `6a62e64` contract + wiring + fencing code; `65deffe` fencing tests;
`5903ca0` late-manifest orphaning (coordinator add-on); `7855a04` canceled-dependency message (add-on);
`9b9a57a` read-only mount flags asserted (found by mutation), plus this report.
Contract text: `docs/architecture/CHECK_ENVIRONMENT.md` (60 lines; `docs verify` ok, 76 files).

## What changed

| Area | Change |
|---|---|
| `agentctl/sandbox.py` (new) | runc OCI bundle: read-only root; read-only host `/usr /lib /lib64 /bin /sbin` (usr-merge links recreated) and `/etc/alternatives`; the check tree at `/work` (rw, chowned to uid 65534); tmpfs `/tmp`, own `/proc`, tmpfs `/dev`, `/dev/shm`; generated `/etc/passwd`+`group`; uid 65534, all capability sets empty, `noNewPrivileges`, seccomp (denies `unshare setns mount ptrace bpf keyctl ...`, `clone` with any `CLONE_NEW*`, `clone3` -> ENOSYS); new pid/net/ipc/uts/mount namespaces; rlimits (nofile, nproc, fsize, core 0); memory/pids/cpu cgroup limits only where the probe shows they work (all three do here, cgroup v1), reported as `limits_enforced`. `probe()`: cached canary run, never raises. `run_in_sandbox(...)`: result dict, deadline -> `runc kill` + `runc delete -f`, bundle always cleaned (refuses to delete if anything is still mounted under it). |
| `agentctl/check_environment.py` (new) | `run_candidate_checks(repo_root, commit, checks, *, label, timeout, spec_fields)`; the shared safe tree materialiser (moved from `integrate.py`, protections kept); `selected_isolation()`; `require_sandbox()`; `environment` record. |
| `scheduler.verify_candidate` | Calls the contract with the candidate result commit (no `.git` link, no leased-worktree execution). Legacy path kept only behind `AGENTCTL_CHECK_ISOLATION=legacy-unsafe`. |
| `integrate._plan` | Calls the same contract with the planned integration commit; intent gains `check_environment`. Tamper hashes remain only on the legacy path. |
| `integrate` fencing | `_finalize` acts under the lease this process claimed; `_renew_expired_lease` verifies in its transaction that the old lease is current AND expired; reconcile never adopts a live lease of another live holder (lease now carries `holder` pid/start-ticks/boot id). |
| `commands/verify.py` | Structured refusal JSON + exit 1 on `sandbox_unavailable`. |
| `scheduler.ingest_manifest` (add-on) | Late manifest naming an earlier/non-current attempt -> orphan evidence + `ContractStale`, active lease untouched. |
| `integrate._preflight_explicit` (add-on) | Accurate message for a task parked as `dependency_canceled`. |
| `gate_runner.py` | Docstring only: now labelled the legacy path. |

## Decisions the coordinator may veto

1. **Risk allow-list now guards only the legacy runner.** Under the default runc sandbox a `high`/`critical` task is verified and its gates rerun (test `test_a_high_risk_task_is_verified_inside_the_sandbox_by_default`). The brief said the allow-list applies to `legacy-unsafe`; if high-risk should still be refused everywhere, it is one condition in `Scheduler.verify_candidate` and `integrate._verify`.
2. **Sandbox failure at integrate is a refusal, not a recorded rejection.** `sandbox_unavailable` leaves no artifact, no event, no parked entry: fixing the host and re-running just works.
3. **A background process left by a check no longer fails the check** (legacy reported `gate_left_processes`); the PID namespace kills it. Both stages behave identically.
4. **Evidence stores hashes and sizes of check output, never text.** (Output text could carry prompt injection into the reviewer.) `run_in_sandbox` returns bounded excerpts to its caller only.
5. **Lease takeover rule.** A live lease whose holder pid is alive (same start ticks) -> `lease_held_by_live_holder`; a live lease with no recorded holder (pre-existing stores) -> `lease_holder_unknown` (wait for expiry); dead holder -> adopted as before (existing recovery tests unchanged).

## Demonstrated (real runc 1.5.2, Linux 6.18, git 2.43, real event store, real Git, real sockets)

Tests (`test_check_environment.py` 18, `test_sandbox.py` 20, `test_ingest_orphan.py` 5, fencing 4, parked message 1), each mapped to the required list:

1. Planted host canary file/dir unreadable at verify and integrate.
2. `rm`/`echo >>` on the real store path by a check: file survives, no marker bytes, `PRAGMA integrity_check` ok; plus a byte-hash-identical test at the sandbox level.
3. A TCP server on the test process's 127.0.0.1 never accepts; TEST-NET addresses and `::1` fail.
4. Listing shows only `bin dev etc lib lib64 proc sbin tmp usr work`; checkout, `.git`, `agent-system/runtime`, another worktree, fake `~/.claude`/`.cashbooks`, `/root`, `/home`, docker sockets, any `agentctl.sqlite3` absent.
5. Timeout kills `setsid`/double-fork stragglers, exit 124, `runc list` empty, no host process left; a background process also dies when the shell exits.
6. `AGENTCTL_RUNC` nonexistent: verify raises `PermissionViolation` (`sandbox_unavailable`) before any transition/event/artifact; integrate (CLI) exits 1 with `sandbox_unavailable`, no event, no branch move, state `integration_ready`; a marker that an unsandboxed run would create is absent (no silent fallback). Invalid `AGENTCTL_CHECK_ISOLATION` value is refused.
7. Same checks give identical verdicts and an identical `environment` object in verify evidence and integration intent, including a Git-dependent check that passes at both stages (it used to fail only at integration); a check that fails only on the merged tree blocks integration (exit 1, branch and task unchanged); timeout vocabulary identical in both stages.
8. Default evidence says `isolation: runc`; `legacy-unsafe` is explicit, recorded in verify evidence and the integration intent, works for low/medium, refused for high/critical before anything runs.
Also: memory cgroup limit actually kills a 900 MB allocation under a 128 MB limit; output is bounded; seccomp blocks `unshare`; `/` and `/usr` carry the `ro` mount flag, `/work` is `rw`.

**Failing-first.** Behavioural tests were written and run against the unchanged wiring (log `/tmp/claude-0/ops/before-ce.log`): 14 of 17 failed/errored, e.g. the canary was readable (`repair_required` instead of `independent_review`), the store file was destroyed (`sqlite3.DatabaseError: file is not a database`), the host TCP server accepted a connection. Passed before and after by design: a failing affected check blocks integration; timeout vocabulary at verify; legacy refusal of high risk. The fencing finalize test fails on old code by completing with the usurper's lease; the late-manifest tests reproduced the revoked-new-lease defect (3 errors) before the fix. `_renew_expired_lease` tests fail on old code only by absence of the function.

**Defects found by the tests while building (all fixed):**
- The first sandbox did not bound output: runc's container stdio are pipes copied by runc, so `RLIMIT_FSIZE` in the container did not apply; a check wrote 6 GB to the output file. Fixed by a file-size ceiling on the runc process itself plus early kill by size polling.
- Mutation showed dropping the `ro` flag on `/usr` went unnoticed (uid permissions hide it). The probe and a test now assert the mount flags.
- Mutation harness caveat: restoring a same-length source edit within one second left a stale `.pyc`; the first full run was invalid (probe wrongly failed) and was discarded. Mutation runs now use `PYTHONDONTWRITEBYTECODE`.

## Mutate-and-confirm (final run, bytecode disabled, file restored after each)

| Guard | Mutation | Result |
|---|---|---|
| read-only mounts | runtime binds `ro` -> `rw` | caught: probe fails (`/usr-not-mounted-read-only`), `ProbeIsNotSilentlySkipped` fails; sandbox behavioural tests then skip (unavailable) |
| network namespace | `network` namespace removed | caught: probe's host-loopback canary fails, same meta test fails |
| fail closed | verify silently falls back to legacy when the probe fails | caught: 2 FailClosed tests |
| verify/integrate consistency | no synthetic repo at integrate | caught: consistency test |
| fencing | `_finalize` re-reads store lease / renewal ignores expiry / live holder adopted | each caught by its own test |
| late manifest | orphan branch disabled | caught: 3 errors |

## Suite

`claude/ops-isolation`, one full run at the end: **465 tests, 1 failure, 1 skipped** (live-provider test), 445 s. Baseline was 417 + 1 skipped.
The one failure is outside my write set and is not a regression in behaviour:
`tests.test_execute.GitHardeningTests.test_config_planted_by_a_gate_check_is_caught_and_stays_inert_afterwards`. It asserts the legacy runner's defence (a check plants config in the SHARED repo; verification fails with `gate_tampered_repository`). Under runc the plant lands in the private synthetic repository and cannot reach the shared one, so the premise no longer holds by default. With `AGENTCTL_CHECK_ISOLATION=legacy-unsafe` the test passes (verified). **Needed change (Lane B / coordinator, `tests/test_execute.py`):** add `@mock.patch.dict(os.environ, {"AGENTCTL_CHECK_ISOLATION": "legacy-unsafe"})` to that test.

Existing tests that assert legacy defences (gate-tree path, tamper hashes, namespace, straggler-as-failure, worktree bookkeeping) now select `legacy-unsafe` explicitly: `test_integrate.py` (2), `test_integrate_hardening.py` (3 classes via `use_legacy_runner`), `test_verify_hardening.py` (`HighRiskRefusalTests`), `test_scheduler.py` (3). No assertion was changed, deleted or skipped. `helpers_lane_b.Lab.candidate` gained additive `risk=` and `verify=` parameters.

## Untested / simulated

- **Simulated:** broken-runc probe cases (a fake `runc` that exits 1; a fake that runs the command on the host, which the canary must reject); stored lease holder identity and expiry rewritten with `Lab.tamper`; the `dependency_canceled` queue entry is a stub (the producer is Lane B's code); a runc start failure via a bad mount source.
- **Untested:** non-root host or rootless runc (probe reports `sandbox_requires_root`); cgroup v2 hosts (only v1 here); non-x86 seccomp architectures; macOS and any non-Linux host (the whole Lab-based suite fails closed there: export `AGENTCTL_CHECK_ISOLATION=legacy-unsafe` to run it); docker backend not implemented and never executed; two concurrent integrators on one store beyond the existing process lock; live providers (none involved).

## Residual risks (what the sandbox does NOT cover)

- Kernel, runc, or seccomp-filter escapes. A kernel bug is a full host compromise: runc runs as **root**, no user namespace.
- The host runtime is readable: all of `/usr` (including `/usr/local`, which on this host holds many third-party tools and dangling links into `/root`), and `/etc/alternatives`. No secret should live under `/usr`; nothing checks that.
- Disk and CPU: the check tree (and tmpfs) are size-bounded per file and `/tmp` is 256 MiB, but the number of files in `/work` and time on the CPU are bounded only by the deadline and cgroup limits that exist (cpu quota 400%). The per-uid `RLIMIT_NPROC` is shared by all concurrent checks (all use uid 65534).
- The tree is a host directory bind-mounted read-write: a check can leave anything in it until the temporary directory is deleted. Output files are hashed, so a check can forge only its own output.
- Verification now trusts the probe's result for the life of the process (cached); a host change mid-run is not noticed until the next process.
- `AGENTCTL_CHECK_ISOLATION=legacy-unsafe` is a full escape hatch to the old, unsandboxed behaviour for low/medium risk by anyone who controls the control plane's environment.
- The event store is still reachable by the control plane itself and by `legacy-unsafe` checks; nothing cross-checks store rows against the event chain (audit F1 remainder).
- Late-manifest rule: a manifest naming neither the active lease/attempt nor any known lease/attempt/earlier epoch still goes through the integrity check and revokes the active lease (unchanged).

## Discovered dependencies (not edited by me)

1. `tests/test_execute.py`: the one-line legacy selection above.
2. `integration_queue.renew_lease` still lacks the in-transaction expiry check; integrate no longer calls it (it uses `integrate._renew_expired_lease`). `integration_queue.complete_integration`/`renew_lease` could adopt the same check and `run_gate_commands` is now used only on the legacy path.
3. `integrate._Git` leaves `agentctl-git-home-*` temp directories behind when a process exits via `os._exit` (the crash helpers); hundreds accumulate in `/tmp`. Pre-existing.
4. CHECKPOINT.md "open findings" about the gate sandbox uid/network namespace and the verify/integrate environment mismatch (audit F1, F2) can now be updated by the coordinator; F1's event-store integrity and the unsandboxed-uid residuals above remain.
5. Schema proposal (not applied): none required; `environment`, `check_environment`, `holder` and the new check keys live in existing free-form payloads.

## Add-on report (late manifest, parked message)

- **Defect reproduced first** (3 errors on the unchanged code): attempt 1's manifest after expiry while attempt 2 held a live lease raised `PermissionViolation(mismatch=fencing)` and revoked the new lease.
- **Fix** in `Scheduler.ingest_manifest`: `_names_superseded_attempt` -> `_record_orphan` + `ContractStale`, before any state check. Tests (real `EventStore`, `tests/test_ingest_orphan.py`): new lease stays active, task stays `running`, `orphan_evidence_count` 1, `late_artifact` event, non-integrable artifact, no `lease_revoked`; the newer attempt can still deliver (reaches the Git binding); a manifest naming only an earlier epoch is orphaned; wrong token on the CURRENT lease and a foreign lease id on the current attempt still revoke. Existing `test_leases` unchanged and green.
- **Parked message:** a `dependency_canceled` parked reason now refuses with "a required dependency was canceled; ... replanned or superseded" (reason code unchanged). `dependency_canceled` is not produced anywhere in this tree; the test stubs the queue entry (simulated).
