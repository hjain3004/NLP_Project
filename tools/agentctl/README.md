# agentctl

The CashBooks development-only control-plane CLI. It owns task state, dependency
resolution, path and semantic locks, leases and fencing tokens, context
compilation, reviewer independence, human-approval verification, gate
evaluation, and the append-only event store. Models produce typed artifacts;
**only `agentctl` changes state.**

`agentctl` is never linked into, imported by, or shipped with the CashBooks
application. It is Python 3.12+, standard library only (see ADR-0002 and
ADR-0003).

## Current delivery status — 2026-11-04

Automated routing, provider execution and serialized integration now exist and were
observed working together **once, for one provider path**, in an isolated delivery clone.
Evidence: `docs/program/runs/2026-11-04-automation-delivery/` (`reports/demo-1.md`,
`reports/demo-2.md`, `evidence/demo*/`).

What works (ordinary CLI):

* `agentctl run <task>` — capability routing from the registry, bounded `claude -p` worker with
  **no tools** (`proposal_only`), scope-audited proposal committed in a leased worktree, manifest
  built from lease/provider facts, real session/run ids and provider-reported usage recorded.
* `agentctl verify <task>`, `agentctl review run <task>` — deterministic checks, then a fresh
  independent reviewer session; identity independence enforced by the existing machinery.
* `agentctl integrate [task]` — deterministic queue head only; checks rerun on the resulting tree;
  the reviewed candidate commit is preserved (fast-forward or `--no-ff` merge); intent/CAS/complete
  ordering with reconciliation; failures never advance the integration branch.

What was demonstrated live: two disjoint real backlog fixes ran concurrently (about 146 s of
overlap, one sample), were verified, independently reviewed in fresh Opus sessions, and
integrated unchanged. A first cycle was also recorded, where the independent review caught a real
defect in a candidate and the integrator refused a task whose checks needed `git`. Provider-reported
cost for both cycles was about $2.66. This establishes nothing about speedup or schedule.

What is **not** established:

* Only the Anthropic CLI path exists. `openai`/`google` registry bindings report
  `unsupported_provider`. Foundation Models / Apple-platform behavior is untouched.
* With the **real** registry every `standard_implementation` task is refused with
  `reviewer_unavailable` (its reviewer class is bound to a provider with no CLI here). The demo used
  a delivery-clone-only registry profile; the real registry was not changed (owner decision).
* Gates and checks run as the control-plane user; there is no OS sandbox. Isolation uses
  `unshare --user --pid` where available (`gate_isolation` is recorded). `verify` runs checks in a
  worktree with a `.git` link while `integrate` runs them in a `.git`-less copy, so a check that
  needs `git` can pass verification and fail integration.
* `repair_required` has no authorization path, so a rejected candidate needs a new task. Superseded
  tasks stay in the queue. Duplicate-request and interrupted-integration behavior is covered only by
  local tests (some simulated), not by the live demonstration.
* G0 has **not** closed. See `docs/program/runs/2026-11-04-automation-delivery/reports/coordinator-g0-lead-classification.md`.

## Running it

From the repository root:

```sh
eval "$(make -C tools/agentctl env)"   # exports PYTHONPATH
python3 -m agentctl <command> [args]
```

The single canonical test invocation, used by every task in the P0 plan and by
the G0 verification:

```sh
make -C tools/agentctl test
```

`make -C tools/agentctl check` additionally runs `python3 -m agentctl doctor`
(the `doctor` command arrives in Task 17).

### Strong local authentication boundary

`agentctl.local_authentication` contains the macOS LocalAuthentication adapter for
consequential approval flows. It receives the action, target type and ID, exact
lowercase SHA-256 target hash, scope, a reason digest, and the generated approval
identity and nonce so the human signal is bound to the exact record that will
later be MAC-sealed. The biometric prompt labels each target field explicitly;
free-form reason text is represented only by its digest, so document text and
PII are not sent to the helper. It never receives seal-key bytes.
The adapter fails closed on unsupported platforms, unavailable services, helper
errors, failed prompts, or cancellation. Tests inject its narrow adapter protocol,
so no test invokes a biometric or passcode prompt. The currently accepted ordinary
approval path remains `interactive_tty_with_key_unlock`; enabling this adapter for a
consequential action is a separately bounded authorization change.

The governance and G0 corrections are recorded as non-authoritative proposals until
human approval:

```sh
PYTHONPATH=tools/agentctl python3 -m agentctl hash --json --digest-only \
  agent-system/work/P0/proposals/governance-change-set.json
PYTHONPATH=tools/agentctl python3 -m agentctl hash --json --digest-only \
  agent-system/work/P0/proposals/G0-criteria-candidate.json
```

## What is built at P0

`agentctl` is delivered in layers, each its own task with its own executable
proof:

| Layer | Task | Modules |
|---|---|---|
| A primitives | 9 | `errors`, `config`, `hashing`, `jsonschema_subset`, `models`, `cli`, `adapters/base`, `commands/hash` |
| B static validation | 12 | `validate`, `commands/validate` |
| C event store | 13 | `store`, `events`, `commands/status` |
| D graph and locks | 14 | `dag`, `locks` |
| E leases + scheduler | 15 | `leases`, `scheduler`, `commands/{dispatch,ingest,ready,debug}` |
| F context + review | 16 | `context`, `review`, `commands/{context,review}` |
| G approval + gate | 17 | `approval`, `gate`, `commands/{approve,gate,doctor}` |
| H integration + routing | Immediate delivery target (2026-11) | `routing`, `execute`, `adapters/claude_cli`, `integrate`, `integration_queue`, `gate_runner`, `commands/{run,integrate,review}` — implemented for the Anthropic CLI path only |

### Layer A surface (this task)

* **`hashing`** — the two normative rules from `agent-system/README.md`:
  `canonical_json`, `sha256_of_json`, `sha256_of_json_excluding`,
  `sha256_of_file`, `sha256_of_tree`. Key-order independent; a self-referential
  hash field is computed over the object with that field removed.
* **`jsonschema_subset`** — exactly the ADR-0003 keyword subset. Every rejection
  is `(json_pointer, keyword, message)`. An unsupported keyword raises
  `UnsupportedKeyword`; it never silently passes.
* **`errors`** — one class per `error-taxonomy.json` category plus
  `SchemaUndeterminable`, `UnsupportedKeyword`, `IllegalTransition`,
  `NotImplementedAtLevel1`.
* **`config`** — repository root, `agent-system/runtime/` (created on first use,
  git-ignored), and the seal-key path, which is refused if it resolves inside
  the repository or any worktree.
* **`models`** — a frozen dataclass per artifact type and the normative
  task-state enumeration.
* **`cli`** — a thin dispatcher over one-file-per-command modules under
  `commands/`, so later layers add a file and never edit the dispatcher.
* **`adapters/base`** — the provider-neutral
  `run(role, context_pack, task_contract, tool_policy, budget, output_schema)
  -> RunResult` signature and `ManualProposalAdapter`, which writes a dispatch
  pack to disk and reads a returned artifact back.

## Layout

```text
agentctl/            the package
  commands/          one subcommand per file
  adapters/          provider-neutral worker adapters
tests/               unittest suites, discovered by `make test`
Makefile             env / test / check
```
