# Ops lane A, report 2 -- repair of the security-review findings, consolidation with lane B

**Branch:** `claude/ops-isolation` (worktree `/home/user/wt/iso`), from `5f93019`. No push, no history rewrite.

## Commits

| Commit | What |
|---|---|
| `f20ee1b` | Merge of `claude/ops-lifecycle` (`0fed469`). **No textual conflicts** (git merged cleanly; Lane A and Lane B touched disjoint files). One *semantic* conflict, below. |
| `660e433` | Fix 1: seccomp socket-family allow-list, probe assertion, tests. Lane B hand-off: `queue_empty` shows `parked_explanations`. |
| `00691cb` | Fix 3 (risk refusal restored) and Fix 2 (per-check pristine tree), `CHECK_ENVIRONMENT.md`. |
| (next) | Fix 4: lifecycle race. |
| (next) | Semantic merge conflict: `test_lifecycle` demo-002 test selects the legacy runner. |
| (this commit) | This report. |

## Merge

* Textual conflicts: none.
* Semantic conflict, found only by the full suite after the merge: Lane B's
  `test_lifecycle.SupersedeFromIntegrationReadyTests.test_demo_002_anomaly_a_rejected_task_is_retried_on_every_head_move_until_it_is_superseded`
  builds a task that passes `verify` (worktree with `.git`) and fails at integration (`.git`-less copy). Lane A's single
  check-environment contract removes exactly that mismatch, so the first `integrate` now integrates `T-a`. Resolution: the
  test selects `AGENTCTL_CHECK_ISOLATION=legacy-unsafe` (the premise it documents); assertions unchanged. A genuine
  contract-level equivalent (a check that fails only on the merged tree) is already covered by
  `test_check_environment.OneContractTests`.
* Lane B hand-off: `integrate`'s `queue_empty` result keeps `parked_without_new_evidence` (reason codes; existing
  assertions untouched) and adds `parked_explanations` (`QueueEntry.parked_explanation`, the remedy text). Test added in
  `test_integrate_queue` (failed with `KeyError` first).

## Fix 1 (HIGH): `AF_VSOCK` escaped the network namespace

* seccomp: `socket` and `socketpair` succeed only for `AF_UNIX`(1), `AF_INET`(2), `AF_INET6`(10), `AF_NETLINK`(16). Every other
  family, including values with bits set above bit 4 or in the upper register half, fails `EPERM`. Implemented as one
  equality rule per denied family below 17 plus `>= 17`; a single rule with `>=3 and <=9` was **mis-compiled by this
  libseccomp (it denied every family)**, found by running it, so ranges are not used.
* Added to the denied syscalls: `io_uring_setup/enter/register` (kernel 6.18 `IORING_OP_SOCKET` opens sockets without
  passing `socket(2)`; `io_uring_setup` succeeded in the sandbox before) and `socketcall` (32-bit multiplexer).
* Probe asserts `EPERM` for families 40, 38, 17, 31, 9, 0, 255 (creation only) and that unix/inet/inet6 sockets can be created.
  No code in this change connects to a vsock peer; no probe discovers vsock services.
* Failing first (old `sandbox.py`): `socket(AF_VSOCK)` was **created** inside the sandbox (`CREATED vsock socket`),
  `socketpair(AF_VSOCK)` returned `ENOTSUP` (family reachable), `io_uring_setup` returned a descriptor.
* Tests (`test_sandbox.HostIsolationTests`): families cannot be created via `socket`/`socketpair` and io_uring is refused;
  unix socketpair, `AF_UNIX` bind/connect, loopback inet inside the namespace, netlink, `multiprocessing` still work; the
  profile arithmetic allows exactly `{1,2,10,16}` over 0..255 and wild values. The existing network test still shows an
  `AF_INET` socket cannot reach a host listener.
* Mutate-and-confirm: with the `socket`/`socketpair` rules neutralised (rule names replaced by a nonexistent syscall) the
  probe fails `socket-family-filter`, `ProbeIsNotSilentlySkipped` fails and the behavioural sandbox tests skip as
  unavailable (probe fail-closed); the behavioural tests themselves fail on the old code (above).

## Fix 2 (MEDIUM): earlier check rewriting the shared tree and synthetic repository

`run_candidate_checks` now rebuilds, **for every check**, a fresh tree from Git objects plus a fresh synthetic repository
(`git init/add/commit` in a new container), and removes it afterwards. Verify and integrate share the function, so both are
covered. Failing first: 4 of the new tests failed (reviewer's pair passed check 2; state leaked: scratch file, deleted
file, branch, tag, config, extra commit). After: the pair `echo good > lint.txt; git add -A; git commit --amend` then
`grep -qx good lint.txt && git diff --quiet HEAD` fails on check 2 in both stage labels and in a real `verify_candidate`
(nothing advances); a following check sees pristine file and repository state; a check can still write scratch files and
commit within itself. The legacy runner still runs all checks in one tree (documented).

## Fix 3: high/critical refused again

`Scheduler.verify_candidate` and `integrate._verify` refuse `high`, `critical` and unknown/missing risk under every
isolation, before any transition, event, artifact or check (`gate_risk_not_permitted_until_sandbox_reviewed`; unknown risk
keeps `unsandboxed_gate_unknown_risk`). The hardening-1 tests pass unchanged except the renamed reason code. The
decision-1 test was replaced by default-path tests (verify: before events/artifacts/check; integrate: rejected, branch
unmoved, gate never ran); both failed first. `CHECK_ENVIRONMENT.md` says the refusal holds until the boundary has had a
further independent review and an owner decision. The old names `unsandboxed_gate_refusal`/`UNSANDBOXED_GATE_ALLOWED_RISKS`
remain as aliases.

## Fix 4: flaky `test_parallel_identical_authorizations_produce_exactly_one`

Never failed in isolation; under 12 CPU burners on 4 cores it failed **2 of 15** runs: the child's refusal reason is
printed on **stdout** (exit 3), not stderr, which is why it was unprinted. Reason: `evidence_already_used`. Race in
`lifecycle.authorize_repair`: the winner committed after the loser's second `replay()` and before its `spent` read, and the
`spent` branch refused. Fix: that branch replays when the existing authorization is this request's own (different evidence
naming the same content stays refused). After: **0 of 25** under the same load. Deterministic regression
`test_an_identical_request_that_commits_between_the_replay_check_and_the_spent_check_is_a_replay` (simulated interleaving) fails
before, passes after. The parallel test's assertion message now prints each child's stdout and stderr.

## Suite

One full run was made at the end of the work, then a second after the single failure it found (the semantic conflict above)
was fixed, so the green result is from a full second run: `claude/ops-isolation`, `python3 -m unittest discover -s tests -t .`
in `tools/agentctl`: **551 tests, 0 failures, 1 skipped** (live provider), 603 s. First run: 551 tests, 1 failure (the
demo-002 test), 584 s. `agentctl docs verify` ok.

## Residual risk / not covered

* Family filtering is a syscall filter, not a namespace: a kernel/seccomp bug, or a syscall that creates sockets that this
  profile does not know of, bypasses it. Other host-reaching syscalls are denied by name, not by allow-list.
* Information leak: `/proc/self/mountinfo` inside the sandbox lists host paths of the bind sources (temporary tree, runtime
  directories). Documented, not fixed.
* Still true from report 1: kernel/runc escapes; runc runs as root; readable `/usr`; `legacy-unsafe` escape hatch; disk/CPU
  bounded only by deadline and cgroups; cgroup v1 only; x86_64 only tested (the 32-bit ABI is covered by name, not tested).
* The repair has not been independently reviewed; high/critical stay refused for that reason.
* Per-check rebuild costs one materialisation and one `git add` per check (about one extra second each in tests).
* A duplicate of the Fix 4 race may exist in the other lifecycle commands (`supersede`); only the repair path was reproduced.
