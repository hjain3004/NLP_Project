# agentctl

The CashBooks development-only control-plane CLI. It owns task state, dependency
resolution, path and semantic locks, leases and fencing tokens, context
compilation, reviewer independence, human-approval verification, gate
evaluation, and the append-only event store. Models produce typed artifacts;
**only `agentctl` changes state.**

`agentctl` is never linked into, imported by, or shipped with the CashBooks
application. It is Python 3.12+, standard library only (see ADR-0002 and
ADR-0003).

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
consequential approval flows. It receives only the action, the exact lowercase
SHA-256 target hash, and the displayed reason; it never receives seal-key bytes.
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
| H integration + verify | P1 entry | `verify`, `integrate`, `routing` — **stubs only at P0**, raising `NotImplementedAtLevel1` |

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
