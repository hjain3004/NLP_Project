# Ops Lane B report 1: real registry usable; repair and cancellation lifecycle

Worktree `/home/user/wt/life`, branch `claude/ops-lifecycle`, base `81063b15f3249a2fbda7b1077a6d5112a02d646d`.
Commits (code and docs, none pushed): `296268a` (registry candidate + routing), `d3a489f` (run/review plumbing and
disclosure), `9c42d27` (owner-decision document), `be67a4b` (lifecycle), `5c6a60e` (cancellation loop bound).
This report is a separate docs-only commit after them. No lease, epoch or fencing token was issued for this work, so
there is no leased-attempt artifact to echo; the control plane has not bound this report to a dispatch.

**Full suite:** `make -C tools/agentctl test`, one run, from branch `claude/ops-lifecycle` at `5c6a60e`: **472 tests, OK, 1 skipped** (the opt-in live-provider test), 324 s. Base `81063b1` had 417; this lane adds 55. `agentctl docs verify`: ok (76 files). No other suite was run in parallel by this lane (another lane's suite was running on the same host, which slows the fsync-heavy integrate tests).

## 1. Task 3: the real registry is usable, by explicit opt-in only

| Change | Where |
|---|---|
| Disclosed candidate entry: `complex_implementation` / `anthropic` / `claude-opus-5-5` / `unpinned` / `binding_type: candidate` / `candidate_status: unevaluated` / `promotion_requires` -> `champion-challenger-v1`. The five `initial_prior` entries are untouched (a test compares them with the base commit). | `agent-system/routing/capability-registry.json` |
| Champion first. A candidate is routed only if the champion has no runnable adapter (`unsupported_provider` or `executable_missing`) and the operator opted in with `--allow-candidate-bindings` (`run`, `review run`) or `AGENTCTL_ALLOW_CANDIDATE_BINDINGS=1` (exactly `1`). Without it the refusal names the candidate, the flag, the env var, `champion-challenger-v1`, and says promotion is an owner decision. `RouteDecision` gains `champion_unavailable_reason` / `candidate_offered`; `TaskRoute.candidate_roles`. Two candidates for one class are refused as ambiguous. | `routing.py`, `commands/run.py`, `commands/review.py`, `execute.py::parse_run_options` |
| Disclosure. Run record: `route.*.binding_type`, `champion_unavailable_reason`, and `candidate_bindings{roles,statement,bindings}`. An author candidate adds a `deviations` line to the implementation manifest. A reviewer candidate adds a non-blocking control-plane finding `CP-CANDIDATE-BINDING-1` ("[control-plane note, not a reviewer finding] ... unevaluated candidate ...") to the stored verdict, which has no free-text field and `additionalProperties: false`. `review run` output carries `reviewer_binding_type`. | `execute.py` |
| Unchanged: reviewer floor, role-collision and session-independence checks, the served-model rule. No model name is in runtime code (a test greps the package for every registry model id). | |
| `docs/program/runs/2026-11-04-automation-delivery/reports/registry-owner-decision.md`: the decision (promote / evaluate / bind elsewhere / do nothing), the served identifiers from the four demo review records, what each option changes, and what the evidence does not establish. No approval is claimed. | docs |

Demonstrated (real registry file, real git, real store, simulated `claude` executable): refusal without opt-in (`reviewer_unavailable`, message names
`claude-opus-5-5` and the flag, no lease, no provider call); with opt-in author `claude-sonnet-5` (champion) and reviewer
`claude-opus-5-5` (candidate) both route, `run` reaches `independent_review`, `review run` reaches `integration_ready`, argv `--model claude-opus-5-5`;
the champion wins when it is runnable even with the opt-in; `openai`/`google` classes without a candidate stay `unsupported_provider` with the opt-in;
a candidate whose CLI is missing reports `executable_missing` with the champion's reason; a served-model mismatch on the candidate fails `model_unavailable`
and ingests nothing; env var `1` works and `0`/`true`/empty do not; the CLI flag and env var work end to end.

Not demonstrated: any live call. The candidate model has not been run through this code path against a live provider by this lane; the only live
evidence is the four demo reviews, which ran under the delivery-clone profile (`binding_type: demo_environment_profile`).

## 2. Task 4: repair and cancellation lifecycle on the pinned 24 rows

New: `lifecycle.py`, `commands/task.py`. Modified: `execute.py` (repair feedback), `integration_queue.py`. No state-machine row, schema or policy was added
or changed; the lifecycle imports `scheduler.transition` and uses `EventStore.execute` only.

### `agentctl task repair <id> --evidence <review-id|artifact-id|attempt:ID> --note TEXT [--operator ID]`
- Legal only from `repair_required` with `attempts_remaining > 0`; it never touches a budget (`dispatch` spends the attempt).
- Evidence must be about the task's **latest attempt** and be one of: a review with a blocking finding; a failed `verification-evidence` artifact; or `attempt:<id>`
  of an ended (`failed`/`revoked`) attempt (my addition: a scope-audit failure has no review and no verification record, so without it that state is a dead end).
- The same evidence returns the existing authorization (`already_authorized`, no event). Evidence already spent on an earlier attempt is refused `evidence_already_used`.
- Records a `repair-authorization` artifact (evidence kind/ref/hash, bounded 1000-char note, operator string, previous attempt, feedback) and performs `repair_required -> ready`
  in one transaction with the `repair_authorized` event. The operator string is self-declared and unauthenticated; this is **not** a human-approval record.
- `run` then puts the stored feedback in the prompt: the reviewer's blocking findings, or the failed command and exit code, plus the rejected diff (code only); never
  the author's summary, assumptions or risks. It records `repair.authorization_id` in the run record and `repair_authorization_id` in the result.

### `agentctl task supersede <old> --by <new> --reason TEXT` / `task cancel <id> --reason TEXT` / `task status-chain <id>`
Shortest legal chain to `canceled`, computed by breadth-first search over the live table using only `dependency_changed`, `context_rebuilt`, `contract_invalid`,
`replacement_plan_accepted` (never `authority_decision`):

| Source state | Chain | Steps |
|---|---|---|
| `draft`, `validated` | `contract_invalid` -> `replan_required` -> `replacement_plan_accepted` -> `canceled` | 2 |
| `ready`, `candidate`, `deterministic_verification`, `independent_review`, `integration_ready`, `repair_required`, `blocked`, `escalated` | `dependency_changed` -> `stale` -> `context_rebuilt` -> `validated` -> `contract_invalid` -> `replan_required` -> `replacement_plan_accepted` -> `canceled` | 4 |
| `stale` | `context_rebuilt` -> `contract_invalid` -> `replacement_plan_accepted` | 3 |
| `replan_required` | `replacement_plan_accepted` | 1 |
| `canceled` | no-op | 0 |
| `leased`, `running` | refused: a live author lease would be orphaned mid provider call; expire it first | |
| `integrating` | refused: an integration is in flight on the Git branch | |
| `completed`, `failed` | refused: terminal | |

Every step is its own transaction and event with reason `superseded_by:<new>` (or `operator_canceled`), referencing the `task-cancellation` decision artifact written in step 1.
The event names are the table's, not facts about the task (no context was rebuilt); the reason code and decision artifact are what distinguish this chain from a real interface change.
Resumable: each iteration re-plans from the live state; steps are idempotent per `(task, event, from_state)`; a different decision is refused (`conflicting_decision`); a
first step that loses a race to a different writer is refused (`state_changed`), so concurrent repair and supersede have one winner. Nothing is deleted.
Preconditions: replacement exists, same work package, nonterminal, does not (transitively) depend on the old task; no dependent holds a live lease or is integrating.
Dependents: with `--by`, dependency rows are re-pointed old -> new (merged if the dependent already depends on the replacement) and a nonterminal dependent goes `stale`
(`dependency_changed`); without it a nonterminal dependent goes `blocked` (`dependency_unavailable`) and keeps naming the canceled task. Terminal dependents are left alone.
Only direct dependents are resolved.

### Integration queue
`integration_queue.py`: `QUEUE_INELIGIBLE_STATES` (documented, `canceled`/`stale`/`blocked`/`replan_required`/`repair_required`/`escalated`/`failed` never enter), and a
candidate whose dependency is `canceled` is parked as `dependency_canceled` instead of being retried (and re-rejected) on every head move.
The demo anomaly is reproduced and fixed by a test: `T-a` (passes `verify`, fails `integrate`) is retried after the head moves (two rejection artifacts); after `task supersede`
the same sequence of `integrate` calls never selects it and writes no further rejection.

## 3. Demonstrated, simulated, untested

**Demonstrated** (real `EventStore`, real git worktrees, real `python -m agentctl` processes): repair happy path to `ready` then a real `run_task` reaching `candidate` with the
findings in the prompt and the authorization id in the run record; repair refused for wrong state, exhausted budget, unknown/non-blocking/passing/over-long input, and spent evidence;
idempotent replay; six parallel authorizations of the same evidence yield exactly one; supersede from `integration_ready` and from `repair_required` to `canceled`; history
preserved (event count +1 per step, no table shrinks, earlier events byte-identical); dependents re-pointed, merged, blocked, skipped, and in-flight refused; four parallel identical
supersedes walk each step and resolve each dependent once; two supersedes with different replacements have one winner; a repair that commits first beats a supersede that planned earlier;
the DEMO-002 queue anomaly; late manifest for a superseded attempt becomes orphan evidence (count 0 -> 1, state stays `canceled`, one `late_artifact` event, integrable manifests unchanged).

**Simulated** (labelled in the tests): the provider (a fake `claude` executable) for every `run`/`review run`; the crash *instant* in the interrupted-supersede tests (`os._exit` in a hook, real
child process and store) and the forced interleaving in the repair-vs-supersede test; direct state writes through `Lab.tamper` for a completed dependent, a leased dependent, a pre-canceled task
and a canceled dependency of an integration-ready task.

**Untested / not exercised:** any live provider call by this lane; `task repair`/`supersede` against the store of the delivery clone `/home/user/delivery` (not touched); supersede from
`draft`, `validated`, `stale`, `blocked`, `escalated`, `deterministic_verification`, `independent_review` and `replan_required` as real executed chains (planned and asserted only by `plan_cancel_chain`,
which is derived from the live table); transitive (non-direct) dependents; a supersede racing a live verifier or reviewer process (their ingest would be refused by the state check, but this was not run);
what happens to a `stale`/`blocked` dependent afterwards (see D3).

## 4. Regression evidence (AGENTS.md section 8)

- Tests were written before the implementation. The module-level run failed (import error) before it existed; the anomaly part of the queue test was first reproduced on unchanged code with a scratch script
  (rejection artifacts for `T-a` = 2, third `integrate` call re-selects it).
- Mutate-and-confirm (each mutation applied, focused tests run, file restored):
  1. `candidate_bindings_allowed` always `True`: 8 failures across `CandidateBindingTests` and `CandidateBindingRunTests`.
  2. attempts guard removed from `authorize_repair`: `test_never_extends_the_attempt_ceiling` fails.
  3. dependents never resolved: 3 failures in `SupersedeDependentsTests`.
  4. `dependency_canceled` parking disabled: `test_a_candidate_whose_dependency_was_canceled_is_parked_not_retried` fails.
  5. the final cancel step does not write `canceled`: the chain livelocked (replayed the same idempotent step until the 110 s timeout). That exposed a real defect, now fixed (`5c6a60e`: bounded loop, reason `no_progress`);
     with the fix the anomaly test fails fast with `no_progress`.

## 5. Discovered dependencies and findings (not fixed; outside the write set)

- **D1, Lane A (`scheduler.py::ingest_manifest`):** a late manifest from an expired attempt while a newer attempt is `running` is treated as a fencing mismatch: it revokes the **new** lease and sends the task to
  `repair_required` (reproduced: `PermissionViolation mismatch='fencing'`, state `repair_required`, orphan count 0). Design 14.1 says an expired attempt's late artifact is orphan evidence with no state advancement.
  The orphan path is taken only when no lease is active, which is the case my test covers (a canceled task). In `leased` state the late manifest raises `IllegalTransition` and is not recorded at all.
- **D2, Lane A (`integrate.py`):** (a) a candidate-reviewed task integrates; nothing reads `binding_type`, although `champion-challenger-v1` says `challenger_merge_eligible: false`. (b) A task parked as
  `dependency_canceled` gets the generic "retry without new evidence ... branch has not moved" message from explicit `integrate <id>`, and the `waiting_on_dependency` follow-up text ("integrate the required dependencies first")
  is wrong for a canceled dependency.
- **D3, Lane A / owner:** a `stale` dependent (after re-pointing) cannot leave `stale`: `ContextCompiler.rebuild` requires an interface-content change and refuses otherwise, and no command exercises `admission_passed`
  (`validated -> ready`) or `blocker_resolved` (`blocked -> ready`). Dependents of a superseded task therefore wait for a Lane A command; this is the intended "context must be rebuilt" step but it has no exit yet.
  I did not call `context_rebuilt` for them.
- **D4, coordinator:** `context_rebuilt` is used as an event name inside the cancel chain although no context is rebuilt. If the owner wants the table to say what happened, a dedicated cancel row is a state-machine change
  (design 14.1), which this lane was told not to make.
- **D5:** a `complex_implementation` task still cannot be independently reviewed (same-class `actor_role` collision); opting in to the candidate does not change that.
- `agentctl events export`/`evidence` dry-run replay accepts only the DRY-RUN event vocabulary; events from this lifecycle (`repair_authorized`, the cancel chain, `dependency_repointed`) were not checked against any export verifier.

## 6. Owner decisions

1. The `complex_implementation` binding: promote / evaluate under `champion-challenger-v1` / bind elsewhere / do nothing. See `registry-owner-decision.md`. No approval is claimed.
2. Whether a task whose only independent approval came from an unevaluated candidate may integrate (today it may; the verdict carries the disclosure).
3. Whether a repair authorization should require a sealed human approval record instead of a self-declared operator string; today it is neither (AGENTS.md section 10 lists what always needs one: a repair is not on that list).
4. Whether the ceiling for tasks that reach `repair_required` with `attempts_remaining == 0` after a blocking review should escalate automatically (today `review_blocked` always writes `repair_required`; the lifecycle refuses `attempts_exhausted` and the next rung is escalation).
5. Whether a dedicated cancel row should replace the four-row chain (D4).

## 7. Judgement calls the brief did not settle (flagged, not hidden)

- `attempt:<id>` as a third kind of repair evidence (above).
- A repair's feedback includes the rejected diff (bounded to 60 KB, code only) because the reviewer's findings cite lines of code the repaired attempt would otherwise not see; it excludes everything the previous author said.
- The opt-in applies to whichever role needs it (author or reviewer); an author candidate is disclosed in the manifest `deviations`.
- `escalated` is walked to `canceled` through the operator chain, not through `authority_decision`.
- `task supersede` on an already-canceled task is `resumed_complete` (decision recorded) or `already_canceled` (canceled by other means, no record), never an error.
- Exit codes: 0 done or idempotent, 2 usage, 3 refused with nothing (more) changed, matching `run`.

## 8. Files

Code: `agentctl/{routing,execute,lifecycle,integration_queue}.py`, `agentctl/commands/{task,run,review}.py`, `agent-system/routing/capability-registry.json`.
Tests: `tests/{helpers_lane_a,test_routing,test_execute,test_lifecycle}.py` (`test_integrate_queue.py` needed no change; the queue assertions live in `test_lifecycle.py`).
Docs: this report and `registry-owner-decision.md`. Nothing outside the write set was edited.
