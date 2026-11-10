# Lane A report 1: routing, Claude CLI adapter, `run`, `review run`

Branch `claude/lane-a-routing`, base `3a1457c`. Commits: `0274810` (A1), `a4ca6d3` (integration_order), `51a680f` (A2).
Full suite: 305 tests, 1 skipped (opt-in LIVE), 1 error, which is the pre-existing baseline
`test_validate_valid` / `03-context-manifest.json` (stale `AGENTS.md` hash). It was not touched.

## Files changed

New: `agentctl/routing.py` (rewritten), `agentctl/execute.py`, `agentctl/adapters/claude_cli.py`,
`agentctl/commands/run.py`, `tests/{helpers_lane_a,test_routing,test_claude_cli_adapter,test_execute}.py`.
Modified: `adapters/__init__.py`, `commands/review.py` (additive `run` subcommand), `scheduler.py`
(additive), `plan.py` (one line: `integration_order`), `tests/test_scheduler.py`, `tests/test_leases.py` (additive tests only).
`leases.py`, `store.py`, `review.py`, `adapters/base.py`, schemas, policies, registry: untouched.

## Commands

- `python3 -m agentctl run <task-id> [--timeout S] [--max-budget-usd X] [--export PATH]`
  exit 0 candidate ingested, 1 started and failed, 3 refused before any lease.
- `python3 -m agentctl review run <task-id> [same options]`; `prepare|assign|ingest` unchanged.
- Run record: `agent-system/runtime/runs/<task>/attempt-<n>.json` (author) and `review-<n>.json`; `--export` writes a copy.
  Artifacts for cross-link validation: `agent-system/runtime/attempts/<task>/<attempt>/` (context manifest, implementation manifest, review verdict).

## Behaviour (as built)

- Routing: registry + policy only. Statuses `available | unsupported_provider | executable_missing | unauthenticated | model_unavailable`
  (the last is discovered at invocation, never guessed), plus task-level `reviewer_unavailable` and `capability_floor`.
  Reviewer class: matrix value, or for prose the earliest registered class named; none (`architectural_authority`) raises `HumanGate`;
  `high`/`critical` risk raises the floor to `complex_implementation`. `run` refuses before leasing if the worker, the floor, or the
  policy-required reviewer is not routable. No fallback model is ever passed.
- Adapter: exact argv from the brief plus `--session-id <minted uuid>`; prompt on stdin; own process group; hard timeout kills the group
  and any grandchild, reaps, keeps partial output; child cwd is a fresh empty temp dir. Success needs `is_error` false, `terminal_reason`
  completed, schema-valid `structured_output`, routed model present in `modelUsage` (extra keys flagged, not adopted), CLI session id equal
  to the minted one. Classes: executable_missing, unauthenticated, model_unavailable, quota_or_budget, timeout, nonzero_exit,
  malformed_output, usage_unavailable, provider_error.
- Manifest `input_tokens` = `input + cache_creation_input + cache_read_input` (all three must be reported; live example: `input_tokens: 2`
  alone would have under-reported 14,805 tokens). Raw usage is kept in `model_runs.usage_json` for success and failure; unreported usage is `{}`/None, never 0.
- Identities: session id and run id are minted before invocation; the run id is the `provider_run_id`; the CLI result `uuid` is extra evidence.
  `review run` calls `ReviewManager.assign` before the reviewer process starts.
- Failure paths: timeout / nonzero / auth / model / quota / provider_error -> `expire_lease` -> `ready`; malformed -> `handle_schema_error`;
  `usage_unavailable`, scope-audit rejection, empty change, invalid manifest -> new `Scheduler.fail_attempt` (`repair_required`, or `escalated` with no attempts left).
- Scheduler additions: `task_spec`, `fail_attempt`, `record_model_run`, public `CAPABILITY_RANK`, `CHECK_TIMEOUT_SECONDS`; `verify_candidate` checks
  now run in their own process group with a 600 s timeout (timeout = failing check, `exit_code 124`, `timed_out: true`) and a scrubbed env
  (fresh empty `HOME`, minimal `PATH`, allowlist `PYTHONPATH/LANG/LC_*/TZ`; no `CLAUDE_*`/`ANTHROPIC_*`). `TaskSpec.integration_order` added (default None).

## Demonstrated (live, real CLI, real git, real event store)

- LIVE opt-in test (`AGENTCTL_LIVE_PROVIDER=1`, model `claude-haiku-5-5`): session id equals the minted one, served model equals requested, real result uuid, usage and cost captured.
- One full live cycle in a disposable repo (registry fixture binding both classes to `claude-haiku-5-5`, otherwise real repo data):
  `agentctl run` (exit 0, state `candidate`, 1 file modified, author cost $0.0036) -> `agentctl verify` -> `agentctl review run` (fresh session,
  verdict `no_blocking_findings`, state `integration_ready`, cost $0.0034). Records exported under the scratchpad, not committed.
- Total live spend of this lane: about $0.01 (cap was $1.50).
- Live probes also established: unknown model returns `is_error` true, `api_error_status` 404, `subtype` "success" (exit 1 in this run, 0 in the brief's
  observation, so exit status is not trusted either way); budget exhaustion returns `subtype error_max_budget_usd`, `terminal_reason budget_exhausted`, exit 1.

## Simulated (fake `claude` executable; real git/store/processes)

Timeout with grandchild kill and partial output, nonzero exit, malformed JSON, is_error with exit 0, served-model mismatch, helper-model flagging,
missing usage, 401 auth, budget error, missing structured output, bad/mismatched session id, over-scope proposals (outside writable, `..`, absolute,
glob, prohibited, `.git`, duplicate, symlink escape, empty change), lease expiring mid-run (orphan evidence, no candidate), reviewer failure,
blocking verdict, reviewer role collision, reviewer unroutable, CLI exit codes, env values absent from records and events, fixed commit author.
Regression check: the credential-scrub test fails when the old inherited environment is restored (verified by patching `_check_environment`).

## Untested or not exercised

Live auth failure, live quota exhaustion, live timeout; two concurrent `run`s; SIGTERM unwinding (handler written, not exercised);
`delete` action and glob `readable_paths` through a full run (audit covers delete rejection only); prompt size caps with real oversize inputs;
wrong fencing token via `run` (covered by the existing `test_leases` manifest-tamper test). Whether passing the parent's `CLAUDE_CODE_*`
session variables to the child leaks anything into the parent session is unobservable from here: the child got a distinct session id and
nothing broke, so the environment is passed unchanged (no value is ever logged), but contamination was not ruled out.

## Findings that block or shape the demo (need an owner decision, none fixed here)

1. Real registry: `standard_implementation` -> reviewer `complex_implementation` -> openai, so `run` is refused (`reviewer_unavailable`) for every standard task.
   `mechanical_execution` -> google is unsupported as author. The live demo needs the labelled delivery-repo registry profile.
2. A `complex_implementation` task cannot be independently reviewed: the matrix names the same class, and `ReviewManager.assign` rejects a shared `actor_role` (tested). Policy or machinery decision needed.
3. `work_package_planning` is bound to `claude-opus`, which the CLI rejects (404). Registry correction needs the approval path.
4. `repair_required` has no authorize path (`repair_authorized` exists only in the transition table), so tasks failed by `fail_attempt` or `handle_schema_error` wait indefinitely.
5. With `maximum_artifact_corrections > 0`, `handle_schema_error` returns `correction_pending` and leaves the task `running` under its lease; `run` does not re-invoke (no silent retry), so the task only recovers on lease expiry.
6. Infrastructure failures still spend an attempt; a `maximum_attempts: 1` task cannot be re-run after a timeout. Budget semantics for infra failures need a decision.
7. The registered `context_hash` is bound at plan time (attempt label `<task>-attempt-1`); `run` reproduces it exactly and fails closed on drift.
8. `context.py::_find_task_contract` globs `agent-system/**`, including `agent-system/runtime/worktrees/**`. It is harmless today (a contract cannot contain its own base commit) but would shadow the real contract if a worktree ever held a copy. Suggest skipping `agent-system/runtime`.
9. Lane B request not done by design: new state-machine transitions for integration (pinned 24-row table unchanged; design-spec section 14.1 is an owner matter).

## Schema or approval proposals

None needed for this path: the Claude CLI reports all four required usage fields. A provider that cannot report them would need a nullable-usage
schema proposal (the run fails with `usage_unavailable` and says so). Fixing findings 1 to 3 changes the registry or policy and needs the approval path.

## Discovered dependencies (outside the original write set)

`plan.py` (one line, authorised by the coordinator) and the three test files named in the amendments. Nothing else outside the write set was edited.
