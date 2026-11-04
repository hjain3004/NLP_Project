# Owner decision required: the `complex_implementation` model binding

**Status: OPEN. Nothing in this document is an approval, and no approval is claimed or implied.**
It prepares a decision that only the human owner can make (AGENTS.md section 10; registry
`version_change_rule`; `champion-challenger-v1`). Authored by the Lane B ops worker on branch
`claude/ops-lifecycle` (base `81063b1`). This file is a report; it adds no authority.

## The problem in one paragraph

`agent-system/routing/capability-registry.json` binds `complex_implementation` to
`openai / gpt-5.6-terra` (`initial_prior`), and agentctl has no OpenAI adapter. The routing policy
requires a `complex_implementation` reviewer for every `standard_implementation` task, so with the
real registry **every `standard_implementation` task is refused `reviewer_unavailable`**. The only
live path that exists is the Anthropic `claude -p` adapter. The automation delivery demo got past
this with a labelled profile that existed only in a delivery clone
(`evidence/demo/contracts/registry-demo-profile.diff`). That profile is not a registry change for
`main`.

## What this branch changed (and did not)

- Kept the five `initial_prior` champion entries byte-for-byte unchanged (a test compares them with
  the base commit).
- Added one **disclosed candidate** entry to the registry, which names the model and so is, by the
  registry rule, the only place a model may be named:
  `complex_implementation` / `anthropic` / `claude-opus-5-5` / `unpinned` / `binding_type: candidate` /
  `candidate_status: unevaluated` / `promotion_requires` -> `champion-challenger-v1`.
  Under the registry's own `version_change_rule`, it is a **new routing candidate that inherits no
  historical trust**.
- Routing is champion-first. The candidate is used **only** if the champion has no runnable adapter
  **and** the operator passes `--allow-candidate-bindings` (on `run` / `review run`) or sets
  `AGENTCTL_ALLOW_CANDIDATE_BINDINGS=1`. Without it the refusal names the candidate, the flag, and says
  promotion is an owner decision. Nothing is substituted silently.
- When the candidate is used, the run record carries `binding_type: candidate`,
  `champion_unavailable_reason` and a plain statement; the stored review verdict carries a
  non-blocking control-plane finding (`CP-CANDIDATE-BINDING-1`) saying the reviewer binding is an
  unevaluated candidate. The reviewer-floor, role-collision and session-independence checks are
  unchanged.

Default behaviour is unchanged: without the flag the real registry still refuses standard tasks.

## Evidence that exists (do not read it as an evaluation)

Source: `evidence/demo/run-records/` and `evidence/demo-2/run-records/` in this directory. All four
independent reviews ran under the delivery-clone profile, so their records say
`binding_type: demo_environment_profile`, not `candidate`. The model and the adapter are the same
ones the candidate entry names.

| Review run | Reviewed task | Requested | Served (and canonical) | Extra models | Verdict | Provider-reported cost | Wall |
|---|---|---|---|---|---|---|---|
| `review-DEMO-001` | DEMO-001 (cycle 1) | `claude-opus-5-5` | `claude-opus-5-5` / `claude-opus-5-5` | none | `blocking_findings` (1 blocking, confirmed independently by the operator) | $0.248248 | 23.9 s |
| `review-DEMO-002` | DEMO-002 (cycle 1) | `claude-opus-5-5` | `claude-opus-5-5` / `claude-opus-5-5` | none | `no_blocking_findings` | $0.237456 | 16.6 s |
| `review-DEMO-101` | DEMO-101 (cycle 2) | `claude-opus-5-5` | `claude-opus-5-5` / `claude-opus-5-5` | none | `no_blocking_findings` | $0.272735 | 23.4 s |
| `review-DEMO-102` | DEMO-102 (cycle 2) | `claude-opus-5-5` | `claude-opus-5-5` / `claude-opus-5-5` | none | `no_blocking_findings` | $0.232431 | 17.0 s |

What those records do establish:

- The adapter's served-model rule held on all four: the requested id appeared in the provider's
  `modelUsage`, so the served identifier equals the routed identifier (`claude-opus-5-5`), with no
  helper model flagged. The control plane still applies that rule to a candidate (a test covers the
  mismatch case).
- Each review ran in a fresh session distinct from the author's (`distinct: true`, machine-attested)
  and the authors were `claude-sonnet-5` (`initial_prior`), so the Anthropic reviewer reviewed
  Anthropic-family authors (provider diversity is only a preference in the policy,
  `hard_requirement: false`).
- One review found a real behavioural defect that the candidate's own tests missed (DEMO-001 F1).

What they do **not** establish:

- A representative sample. It is 4 reviews on 4 tasks in 2 cycles of one work package, all in one repository, all
  low/medium risk. `champion-challenger-v1` requires a representative sample, zero critical violations
  and acceptable review burden; none of the three has been measured. `routing/baselines/` is empty and
  `metrics.json` baselines are `null`.
- Any comparison with a champion. The champion reviewer has never run (no adapter), so there is nothing to
  compare against. Review quality, false-negative rate and escaped defects are unknown.
- Fitness for high or critical risk, or for security- and financial-sensitive tasks. Gates already refuse
  those risks unsandboxed; the reviewer's competence for them is untested.
- That the candidate model is unpinned (`model_version: unpinned`): a later provider-side change to what
  `claude-opus-5-5` serves would not be visible in the registry.

## The decision

Choose one (or reject all). Each option states what changes.

### Option A: promote the candidate to champion for `complex_implementation`

Replace the `openai / gpt-5.6-terra` champion (or demote it) with `anthropic / claude-opus-5-5` and
drop the candidate entry.

- Changes: `capability-registry.json` only (models live nowhere else), through the normal approval path
  for a registry change (it is guarded by the `routing:capability-registry-v1` semantic lock).
  `--allow-candidate-bindings` is no longer needed; every `standard_implementation` task becomes
  runnable and reviewable; `complex_implementation` tasks become runnable as authors.
- Does not change: the transition table, schemas, policies, the reviewer matrix.
- Cost of choosing it: it asserts trust the registry rule says does not yet exist (no evaluation). It
  makes every Anthropic author reviewed by an Anthropic reviewer, which is the cross-family review the
  policy only prefers. A `complex_implementation` task still cannot be independently reviewed (the
  matrix names its own class and `ReviewManager.assign` rejects a shared `actor_role`); promotion does not
  fix that.
- Evidence it would rest on: the table above, which is not an evaluation.

### Option B: evaluate under `champion-challenger-v1` first (recommended by the registry rule, not by this worker)

Keep the candidate entry as it is. Run a budgeted evaluation: a representative sample of low and medium
risk tasks, the candidate as a frozen shadow reviewer or author, a deterministic evaluator comparing it
with the champion path, zero critical violations, acceptable review burden.

- Changes: no code or registry change is needed to start recording; what is missing is the evaluation
  protocol itself (what counts as a sample, the deterministic evaluator, the review-burden measure) and
  a place to record results (`routing/baselines/` and `metrics.json` are empty). None exists in this
  repository today; this branch does not build it.
- Interim reality: until promotion, every use of the candidate is an explicit operator opt-in per
  command, recorded as `binding_type: candidate`. Strictly read, `champion-challenger-v1` says a
  challenger is never merged directly; the opt-in path does let a candidate-reviewed task integrate
  (the integrator does not look at the binding type). If you want that closed, it is a Lane A / policy
  decision, not something this branch can do.
- Cost of choosing it: the delivery path stays operator-gated, and the evaluation work is real.

### Option C: bind the class somewhere else (or leave the real binding and build the adapter)

Either keep `openai / gpt-5.6-terra` and implement and exercise an OpenAI adapter, or choose another
provider that has (or gets) an adapter.

- Changes: new adapter code (`adapters/`, `ADAPTER_BINDINGS`), live authentication/invocation/result
  evidence before anything may claim it works (AGENTS.md section 8), and possibly a registry edit if
  the model changes. The candidate entry would be removed or kept as a second candidate.
- Cost of choosing it: the most work, no live evidence now, and `standard_implementation` stays
  refused until it lands.

### Option D: do nothing

Real-registry standard tasks keep being refused unless the operator opts in per command. Nothing
decays: the candidate entry is inert without the flag.

## What the owner needs to provide

1. Which option (A, B, C, D), or a different one.
2. For A: the approval record through the normal path for a registry change, and an explicit statement
   that the evidence above is accepted as sufficient, given that it is not an evaluation.
3. For B: the sample definition, the evaluator, and the budget.
4. For any option: whether a task whose only independent approval came from an unevaluated candidate
   may be integrated (today it may, with the disclosure recorded in the verdict).

## Non-claims

No approval exists or is implied. The candidate is unevaluated. The four demo reviews were run under a
different label in a delivery clone. Nothing here changes the open G0 criteria (G0-12, G0-13, G0-14).
