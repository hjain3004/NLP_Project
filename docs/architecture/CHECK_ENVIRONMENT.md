# Check environment contract

**Status:** Level 3 authority for development-automation checks. Contract `agentctl.check-environment`,
version 1. Implemented by `tools/agentctl/agentctl/check_environment.py` (contract) and
`tools/agentctl/agentctl/sandbox.py` (enforcement). Written for ops lane A, 2026-11-04.

## Why

A contract-owned deterministic check is a shell command written by, or influenced by, a model. Before
this contract it ran at the control-plane uid with absolute-path reach to the event store, credentials,
other worktrees and the shared `.git`, with network access, and `verify` and `integrate` ran it in two
different environments (a `.git` link versus none). Environment filtering and after-the-fact tamper hashes
are not a boundary. Separation of duty (AGENTS.md section 7: `sandboxed_tools` means enforced read/write
and process boundaries) needs one.

## The contract

`run_candidate_checks(repo_root, commit, checks, *, label, timeout, spec_fields)` is the only entry point.
`Scheduler.verify_candidate` calls it with the candidate result commit; the integrator's affected-gate
rerun calls it with the planned integration commit. Identical semantics:

| Aspect | Rule |
|---|---|
| Tree | The commit's tree, byte for byte from Git objects, in a fresh directory outside the repository; no `.git`, no filters, symlinks never followed, duplicate or clashing entries refused. Mounted read-write at `/work`. |
| Sandbox | `runc` bundle: uid/gid 65534, no capabilities, `noNewPrivileges`, seccomp (no `unshare`, `mount`, `ptrace`, `bpf`, `clone` with `CLONE_NEW*`), new PID/network/IPC/UTS/mount namespaces. |
| Filesystem | Read-only root; read-only `/usr /lib /lib64 /bin /sbin` (as they exist) and `/etc/alternatives`; `/work`; tmpfs `/tmp`; own `/proc`; tmpfs `/dev`. Nothing else: no checkout, no `agent-system/runtime`, no `~/.claude`, no `/root`, no `/home`, no container sockets, no shared `.git`. |
| Network | None. Only loopback inside the private namespace; the host's `127.0.0.1` is unreachable. |
| Command | `/bin/sh -c <check>`, working directory `/work`, stdin `/dev/null`, output to files. |
| Environment | `PATH`, `HOME=/tmp`, `LANG`, `TZ`, `PYTHONDONTWRITEBYTECODE`, `AGENTCTL_CANDIDATE_COMMIT`. Never `PYTHONPATH`: a check that needs it sets it in its own command. |
| Git | A private synthetic repository is created inside the sandbox from the materialised tree (`git init`, fixed identity and dates, `git add -A -f`, one commit `check-environment snapshot of <commit>`). `git rev-parse --show-toplevel`, `ls-files`, `status` work. Its `HEAD` differs from the real commit by design; compare with `$AGENTCTL_CANDIDATE_COMMIT`. |
| Deadline | One per check. Expiry is exit `124`, `timed_out: true`, `reason: "timed_out"`; the container and every straggler are killed and deleted. |
| Limits | OCI rlimits (open files, processes, file size, no core). Memory, pids and CPU cgroup limits only where the startup probe shows the host accepts them; `limits_enforced` says which. |

Evidence gains an `environment` object (contract id and version, isolation, network, git mode, shell, cwd,
variable allow-list, timeout, enforced limits, runtime hash from the probe). For the same host and settings
it is identical in the verification evidence and the integration intent (`check_environment`). Check results
keep `command`, `exit_code`, `stdout_hash`, `stderr_hash`, `reason` and add `gate`, `result`, `timed_out`,
`isolation`. Output text is hashed, never stored.

## Fail closed

`sandbox.probe()` runs a canary container: non-root, a planted host file path not visible, expected root
listing only, read-only `/usr`, loopback-only network with a failing connect to a TEST-NET address, no user
namespace. If it fails, no check runs: `verify` raises `PermissionViolation` (`reason_code`
`sandbox_unavailable`) before any transition, and `integrate` refuses with `sandbox_unavailable` without
moving state or the branch. `AGENTCTL_RUNC` selects the binary. The sole escape hatch is
`AGENTCTL_CHECK_ISOLATION=legacy-unsafe` (default `runc`): the previous unsandboxed runner, recorded as
`isolation: "legacy-unsafe"`, refused for any task whose risk is not `low` or `medium`. Any other value is
refused.

## What it does not cover

A kernel or runc vulnerability; `runc` runs as root; the host runtime directories are readable; disk use
and CPU time inside the check tree are bounded only by the deadline; the Docker backend is not
implemented. These are residual risks, not claims.

## Verification

`python3 -m agentctl docs verify` must keep passing; this file is tracked Markdown and its links are
checked. Behaviour is tested in `tools/agentctl/tests/test_check_environment.py` and `test_sandbox.py`.
