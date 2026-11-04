# Lane A report 2: repair cycle after independent review

Branch `claude/lane-a-routing`. Full suite: 316 tests, 1 skipped (opt-in LIVE), 1 error, which is the
pre-existing baseline `test_validate_valid` / `03-context-manifest.json`. Nothing outside the write set was edited.

## Regression evidence (each test written first, run against the old code, then against the fix)

| # | Bug | Test | Before fix | After fix |
|---|---|---|---|---|
| 1 | Child inherited the whole host environment (session ids, `CLAUDE_CODE_USER_EMAIL`, tokens) | `ChildEnvironmentTests.test_child_gets_an_allowlisted_environment_not_the_host_session`, `...child_home_is_an_empty_private_directory...` | FAIL | pass |
| 1b | Pipe-based collection stalled on an escaped descendant | `...escaped_grandchild_holding_stdout_does_not_delay_a_finished_result` | FAIL (waited ~15 s) | pass |
| 2 | `--export` failure after ingest rewrote the run as failed | `ExportPathTests.test_export_failing_after_a_successful_ingest...` | ERROR (`IsADirectoryError`) | pass |
| 2b | Bad `--export` found only after the lease and model call | `ExportPathTests.test_unwritable_export_path_is_refused_before_any_lease` | ERROR | pass |
| 5 | Planted `diff.external` / `core.fsmonitor` / `core.pager` executed under the control plane | `GitHardeningTests` (pre-poisoned config; and config planted by a gate check, then `review run`) | FAIL (marker file created) | pass |
| 5b | `setsid sleep 15 & exit 0` in a gate check stalled `verify_candidate` | `VerifyCandidateHardeningTests.test_a_check_leaving_an_escaped_process...` | FAIL | pass |
| 3 | Parent-symlink redirect (`docs/lane-a/link -> ../../agent-system`) had no test | `ScopeAuditTests.test_a_committed_relative_symlink_to_a_prohibited_area...` | n/a: passed on the old code, so mutate-and-confirm: with the audit's parent-symlink check disabled the test fails; restored, passes |
| 4 | Reviewer-output consistency check untested | `ReviewRunTests.test_duplicate_finding_ids_are_rejected`, `...no_blocking_verdict_with_a_blocking_finding_is_rejected` | same: with the check removed both fail; restored, both pass |

## Changes

1. **Child environment is an allowlist** (`adapters/claude_cli.py::child_environment/child_sandbox`, also used by the routing auth probe).
   Passed by name: `PATH`, locale/`TZ`/`TMPDIR`/`TERM`, `HTTPS_PROXY`/`HTTP_PROXY`/`NO_PROXY`/`ALL_PROXY` (both cases), `SSL_CERT_FILE`/`SSL_CERT_DIR`/`NODE_EXTRA_CA_CERTS`/`REQUESTS_CA_BUNDLE`/`CURL_CA_BUNDLE`,
   `ANTHROPIC_*`, `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_CONFIG_DIR`. Any name with an underscore token in
   {CLAUDECODE, SESSION, REMOTE, INGRESS, DEBUG, DIAGNOSTICS, TEE, EMAIL, ACCOUNT, ORGANIZATION, MESSAGING, ADDITIONAL} is dropped even if allowlisted.
   Values are never read or logged. `GITHUB_TOKEN`, `AWS_*`, `GIT_CONFIG_*`, `CCR_*` and the rest no longer reach the child.
   **HOME**: the child gets a fresh empty directory (the operator's real home carries account data and memory). Live finding: in this container the CLI authenticates
   with only `ANTHROPIC_BASE_URL` + proxy/CA names and an empty HOME (`claude auth status` still reports logged in), so no extra variable was needed.
   On a machine where the CLI authenticates from `~/.claude` files, set `AGENTCTL_CLAUDE_HOME` to a dedicated directory containing only that login.
   Unauthenticated then surfaces as `unauthenticated`; that case was not exercised live.
   Live check (haiku, $0.0016): asked to list all context besides the prompt, the model before the change listed an account-email reminder, git attribution
   (`Co-Authored-By` / `Claude-Session`) and a cloud-session note; after the change none of these appear. What remains is the CLI's own default system prompt
   (harness rules, tool guidance, a memory-directory note pointing into the temp HOME), which is outside agentctl's control. Live end-to-end rerun
   (`run` -> `verify` -> `review run`, haiku) still reaches `integration_ready`.
2. **Evidence output cannot change a run's outcome.** `--export` is validated (directory creatable and writable) before the lease in both `run` and `review run`
   (`RunRefused`, status `export_unwritable`, exit 3). `_finish_record` never raises: write errors come back as `record_error` / `export_error` in the result; a
   successful ingest is never rewritten as failed.
3. and 4. Tests only (see table).
5. **Control-plane git hardening** (`scheduler.py`: `GIT_HARDENING_ARGS`, `hardened_git_environment`, `run_hardened_git`; used by `Scheduler._git` and every git call in `execute.py`):
   `--no-pager -c core.fsmonitor=false -c diff.external= -c core.hooksPath=/dev/null`, `--no-ext-diff --no-textconv` on every diff call (verified: an empty
   `diff.external` alone makes a plain `git diff` fail), and a scrubbed environment (`GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, minimal `PATH` plus git's own directory,
   `HOME=/nonexistent`, no inherited `GIT_*`). `diff_hash` computation is unchanged for normal diffs.
   **Gate checks** (`verify_candidate`): output goes to temp files instead of pipes, the wait is on the check itself with a hard deadline, the process group is killed, and a
   descendant that escaped via `setsid` can no longer stall the verdict. The 600 s timeout and scrubbed environment are kept.
   `claude_cli.run_bounded` got the same treatment (temp files for stdin/stdout/stderr, wait on the child).

## Residual risk and open items

- Git config can still make git run code on paths this change does not cover: clean/smudge `filter.*` drivers selected by a `.gitattributes` inside a worktree (`git add`/checkout), and
  `config.py::_run_git`, which is outside the write set and uses the caller's environment. Neither is reachable by a proposal-only model (the audit forbids `.git` and would need a writable `.gitattributes`),
  but a gate check can still edit the shared config; the real fix is for checks not to share the repository's `.git` (e.g. a separate clone or a read-only bind), which is a design decision.
- A gate check can still do anything the scrubbed environment allows inside the worktree; the scrub removes credentials, it is not a sandbox.
- Items from report 1 still stand (reviewer-floor/registry bindings, `repair_required` has no authorize path, correction-budget behaviour of `handle_schema_error`, attempt budget on infra failure).
- Live spend of this cycle: about $0.015.
