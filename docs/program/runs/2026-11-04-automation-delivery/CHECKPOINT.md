# Automation delivery — coordinator checkpoint

**Owner of this file:** the coordinator (parent) session only. Workers publish uniquely named
reports under `reports/`; they never edit this file.
**Base:** `bc7c369a5b31b91094e48d07a29dfc47daedbd36` (main). **Branch:** `claude/brave-keller-7gd2bc`.
**Authority:** AGENTS.md; `docs/program/AUTOMATION_DELIVERY.md`. This file adds no authority.

## Preflight facts (2026-11-04, cloud container, Linux)

| Item | Observed |
|---|---|
| Python | 3.13.18 |
| Provider CLIs on PATH | `claude` 2.1.318 only. No `codex`, `gemini`, `kiro`. |
| Claude auth | `claude auth status`: logged in (OAuth). Credentials never read. |
| Structured output | `claude -p --output-format json` returns `session_id`, result `uuid`, `modelUsage` (actual served model), `usage` tokens, `total_cost_usd`, `is_error`, `terminal_reason`, `api_error_status`, `structured_output` (with `--json-schema`). |
| No-tool execution | `--tools ""` gives a run with no tools (true `proposal_only`). |
| Registry model ids | `claude-sonnet-5` served as `claude-sonnet-5`. `claude-opus` is rejected (`is_error:true`, 404, `subtype:"success"` — so `subtype` must not be trusted). `claude-opus-5-5`, `claude-haiku-5-5` also served. |
| Registry gap | `complex_implementation` → openai, `mechanical_execution` → google, `architectural_authority` → openai: no CLI here. These classes must report `unavailable`, not be substituted. |
| Baseline | `make -C tools/agentctl test`: 234 tests, 1 error (`test_validate_valid`, fixture `03-context-manifest.json` pins a stale `AGENTS.md` hash from before the 2026-10-22 edit). `docs verify` ok. `git diff --check` clean. Log: `evidence/baseline-test.log`. |

## Design decisions (frozen for the lanes)

1. **Enforcement class for automated workers is `proposal_only`.** The model gets no tools. It
   returns a typed proposal (full-file contents); the control plane scope-audits, writes,
   commits in the leased worktree, and builds the implementation manifest from lease/provider facts.
   `sandboxed_tools` is not implemented: no verified sandbox exists here.
2. **One provider path:** Anthropic via `claude -p`. Others return an explicit
   `unsupported_provider` / `unavailable` result. No substitution.
3. **Identities:** session id and control-plane run id are minted before invocation (`--session-id`), the CLI-echoed session id is verified, the result `uuid` is kept as evidence (amended after plan review: `ReviewManager.assign` needs ids before the run).
4. **Usage:** record provider-reported values; absent means `null`/unavailable, never 0.
   The current manifest requires four numeric usage fields; the Claude CLI reports all four, so no
   schema change is needed for this path. A provider that cannot report them needs a schema proposal.
5. **Integration state is kept in existing tables** (`tasks`, `artifacts`, `events`): no `store.py`
   schema change. Git ref advance uses compare-and-swap (`git update-ref <ref> <new> <old>`) after
   checks pass on a detached integration worktree; an intent artifact is written first so a crash
   between Git and SQLite is reconciled from actual Git state before retry.
6. Bootstrap implementation lanes run **outside** the control plane (no lease/attempt asserted).
   Only the delivery-demonstration tasks run through the control plane.

## Plan review (independent Opus session, read-only): APPROVE_WITH_CHANGES

Accepted and forwarded to the lanes: (1) reviewer floor — with the real registry every runnable
task is `reviewer_unavailable` (complex_implementation→openai, planning→`claude-opus` rejected);
routing reports this honestly; the demo needs an explicitly labeled **delivery-repo registry
profile** binding the reviewer class to `claude-opus-5-5` (a real registry change in main would be
a separate owner decision); (2) integration has no legal failure exit and no lease path — Lane B
runs all checks on a detached worktree while the task stays `integration_ready`, records rejections
as non-advancing events/artifacts, mints an `integration-lease` artifact, and may call
`store.execute` + `transition()`; (3) ids minted pre-run; (4) artifacts written to per-attempt dirs
for on-disk cross-link validation; (5) Lane A also owns the scheduler/lease/review tests;
(6) `verify_candidate` gets timeout + scrubbed env. Non-blocking notes (lease expiry bound,
no `--fallback-model`, child cwd outside repo, `repair_required` dead end) are in the lane briefs.
Baseline error is the unowned stale fixture pin → handled by a separate mechanical worktree.

## Lanes, ownership, dependencies

| Lane | Branch | Owner (model) | Writes (exclusive) | Reads/consumes |
|---|---|---|---|---|
| A routing + provider + review run | `claude/lane-a-routing` | Sonnet worker | `routing.py`, `adapters/claude_cli.py`, `adapters/__init__.py`, `execute.py`, `commands/run.py`, `commands/review.py`, `scheduler.py`/`review.py`/`leases.py`/`store.py` (**single owner of shared internals**), `tests/test_routing.py`, `tests/test_claude_cli_adapter.py`, `tests/test_execute.py`, `tests/helpers_lane_a.py` | — |
| B integration queue | `claude/lane-b-integration` | Sonnet worker | `integrate.py`, `integration_queue.py`, `commands/integrate.py`, `tests/test_integrate*.py`, `tests/helpers_lane_b.py` | scheduler/review/store via public API only; changes to shared internals are requested from the coordinator → Lane A |
| C reviewer/verifier | fresh sessions | independent | none | candidates |
| Coordinator | `claude/brave-keller-7gd2bc` | parent | this file, `tests/test_stubs.py` (placeholder assertions removed before lane base), README status | everything |

Semantic locks: `contract:agentctl-shared-internals` (Lane A owns). Lane B must not edit
`scheduler.py`, `review.py`, `leases.py`, `store.py`, `events.py`, `models.py`.

The lanes share no interface that needs to be frozen first: both consume the *existing* event-store
rows at `integration_ready` (`candidate-binding`, `verification-evidence`, `implementation-manifest`,
`reviews`, `attempts.author_*`). Lane A produces those rows through the ordinary CLI; Lane B's tests
build the same rows with disposable Git repositories through the existing public scheduler/review API.

Sequence: A1 (routing + adapter + `run`) ∥ B1 (integration). A2 (`review run`) after A1.
Review of A1/B1 as each lands. Demo (Priority 1+2 joined) after both are merged to the session branch.
G0 closeout leads run in parallel only where they touch none of the lane-owned files.

## Task states

| ID | State | Commit | Notes |
|---|---|---|---|
| PRE | done | 3a1457c | preflight + plan; pushed |
| PLAN-REVIEW | done | | APPROVE_WITH_CHANGES, all 6 blockers accepted |
| A1/A2 | built, repair 1 running | `claude/lane-a-routing` 0274810..f2e8025 | independent Opus review: NO_BLOCKING; coordinator made env-allowlist (host env/PII reaches child), export-record, 2 test gaps and shared-.git git hardening mandatory |
| B1 | built, repair 1 running | `claude/lane-b-integration` e33b5f9..9d51a9e | independent Opus review: **2 BLOCKING** (gate escapes env scrub via shared .git → poisons later integrator git; gate timeout unenforced under `setsid`) + 4 non-blocking; repair dispatched with PoC-first regressions |
| TRIAL-MERGE | done | scratch | A+B merge textually clean; combined suite 338 OK on a named branch (33 B tests fail on a detached HEAD: helper fragility, in B repair) |
| FIXTURE-PIN | done, merged | edc6fdc | fixes G0-04 and the baseline error; suite 232 OK; G0-01..11 pass dry |
| A2 | blocked on A1 | | |
| REVIEW-A1, REVIEW-B1 | blocked | | |
| DEMO | blocked | | |
| G0-CLASSIFY | pending | | |

## Budget

Platform-enforced ceiling: $90 (parent + children). Exact platform spend is not observable from
inside the session; provider-reported `total_cost_usd` of every live call the automation makes is
recorded in its run record. Preflight probes cost about $0.05 in total.

## Blockers

- Reviewer floor with the real registry (`complex_implementation`→openai): runnable `standard_implementation` tasks are `reviewer_unavailable`. Demo uses a labeled delivery-repo registry profile; real fix is an owner decision.
- Lane B asked for two new state-machine transitions (pinned 24-row table, design §14.1): not added; rejected candidates are parked by artifact. Owner decision.
- `repair_required` has no authorize path; infrastructure failures consume attempts.

## Next executable step

Wait for lane reports; review each as it lands; merge to the session branch; then demo.
