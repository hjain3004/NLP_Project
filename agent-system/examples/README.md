# Example artifacts

Fixtures the validator's own tests run against. They are not documentation — they are the
evidence that `agentctl` accepts what it claims to accept and rejects what it claims to
reject.

## `valid/`

Eight artifacts that **must validate**: a coherent set for one synthetic documentation-only
task, `EX-DOC-001`, covering work package, task contract, context manifest, implementation
manifest, review verdict, integration manifest, phase gate, and human approval.

Every hash field holds a **real** SHA-256 of the content it references, computed with
`agentctl hash`. Placeholders would make these validate for reasons production artifacts
will not.

`EX-DOC-001` is standalone. The dry run at Task 19 uses its own `DRY-*` contracts.

The human-approval example exists because testing only rejections leaves the accept path for
the single artifact G0 depends on unexercised until the gate itself — and the failure mode
most likely to go unnoticed is one that accepts too readily.

## `invalid/static/`

Six fixtures a **stateless** validator must reject, each violating exactly one rule, each
named for what it violates:

```text
01-task-contract-missing-required-field    required-field enforcement
02-task-contract-overlapping-write-prohibit writable ∩ prohibited ≠ ∅
03-task-contract-path-traversal            ".." in a scope path
04-task-contract-secret-access-true        secret_access must be false
05-task-contract-attempts-over-ceiling     maximum_attempts > 3
08-integration-manifest-patch-altered      candidate_patch_unchanged: false
```

Each test asserts a **distinct error code matching the filename**, so a fixture cannot pass by
being rejected for the wrong reason.

## `invalid/stateful/`

Three fixtures that **cannot** be rejected without seeded store state, owned by the subsystem
that can seed it:

```text
06-review-verdict-self-review              reviewer session == author session   (Task 16)
07-implementation-manifest-stale-fencing   superseded fencing token             (Task 15)
09-event-contains-document-text            denylisted key / oversized string    (Task 13)
```

A fencing token is stale only because a newer epoch exists in the database. Looping these
through a stateless validator would force an artificial rule that makes the test prove
nothing.

Fixture 06 carries `independence_attestation.distinct: true` — a **false** claim — so its
rejection proves identity comparison rather than trust in the attestation.
