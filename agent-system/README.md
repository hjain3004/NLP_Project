# agent-system

The machine-readable half of the CashBooks control plane. `AGENTS.md` says what workers must
do; this directory holds what `agentctl` enforces.

```text
schemas/     ten artifact schemas — the contracts every artifact validates against
policies/    invariants, locks, concurrency, budgets, permissions, errors, escalation,
             protected tests, product-data boundary, approval policy, document authority
roles/       one contract per capability class
routing/     capability registry, routing policy, champion-challenger, metric definitions
templates/   fill-in-the-blank artifacts, every required field present as a placeholder
examples/    valid/ must validate; invalid/ must be rejected, each for its own reason
evals/       routing evaluation packets (defined, unpopulated at P0)
work/        work packages, task contracts, gate criteria and results, evidence
runtime/     SQLite state, leases, dispatch packs — git-ignored, never authoritative
```

## Two normative rules

Five later tasks depend on these. They are fixed here so nothing has to infer them.

### 1. Artifact-type discrimination

Every schema declares a required `artifact_type` whose `const` equals the schema's file stem.
`agentctl validate <path>` selects a schema by reading that value. A file lacking it, or
naming an unknown type, exits non-zero with `SchemaUndeterminable`.

Without this, validating an arbitrary path means guessing among ten schemas — and a validator
that guesses is a validator that eventually guesses wrong and passes something it should have
rejected.

### 2. Canonical hashing

```python
canonical_json(obj) = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
sha256_of_json(obj) = sha256(canonical_json(obj).encode("utf-8")).hexdigest()
```

A **self-referential hash field is computed over the object with that field removed**:
`contract_hash` covers the task contract minus `contract_hash`; `context_hash` covers the
context manifest minus `context_hash`. Without that rule a self-referential hash is
undefinable, and it is load-bearing in five tasks.

`sha256_of_tree` walks paths in sorted order and hashes `path\0sha256\n` per file.

Hashes must be stable across machines. A hash that changes with key order is not a hash.

## Git-tracked

Active authority documents; accepted ADRs; protocol schemas; policies and roles; approved
plans and task contracts; completed manifests and review verdicts; compact gate summaries;
routing baselines; synthetic fixtures.

## Git-ignored

Active leases; the SQLite runtime database; provider session IDs; raw transcripts; raw build
logs; temporary worktree metadata; secrets; token and rate-limit counters; temporary document
staging.

**Runtime state is never authoritative.** It must be reconstructible from committed artifacts
and Git history. `agent-system/runtime/` does not survive a clone — `agentctl` creates it on
first use rather than failing.

## Schema constraints a JSON Schema cannot express

Enforced in `validate.py`, documented in each schema's description:

- `writable_paths ∩ prohibited_paths = ∅`
- `risk: critical` forbids `enforcement_class: unsandboxed_tools`
- `base_commit: "unbound"` only while `status` is `proposed`
- an event is rejected if any key matches
  `[text, content, body, transcript, reasoning, raw, pii, prompt]`
  or any string value exceeds 2048 bytes

## Version

`VERSION` tracks the artifact-protocol version, not the product. Bumping it is a schema
commitment and requires human approval.
