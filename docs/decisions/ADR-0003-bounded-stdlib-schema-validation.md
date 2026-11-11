# ADR-0003 — Bounded standard-library schema validation

## ID and title

ADR-0003 — Bounded standard-library schema validation

## Status

status: superseded by ADR-0006

## Context

Every artifact in the control plane validates against a JSON Schema draft 2020-12 document.
Validation is load-bearing: it is the first gate an artifact passes, it produces the
machine-readable errors that make the single bounded resubmission opportunity meaningful, and
it runs before any candidate-controlled command.

The standard library has no JSON Schema validator. ADR-0002 commits to standard library first.

## Considered options

**Pin `jsonschema` from PyPI.** Complete, well-tested, handles the whole specification.
Rejected for now: it introduces the project's first third-party dependency, a supply-chain
surface, and a version to track — for a validator whose full generality this project does not
use. The design review noted that a compromised dependency invalidates apparently
deterministic verification, which is exactly the property validation is supposed to provide.

**Hand-rolled ad-hoc checks per artifact type.** Rejected: ten artifact types with divergent
validation logic is ten places for a rule to be forgotten.

**A documented subset of draft 2020-12.** One validator, one set of semantics, explicitly
bounded.

## Decision

`agentctl.jsonschema_subset` implements exactly these keywords:

```text
type   required   properties   additionalProperties   items
enum   const      pattern      minItems               minLength
maximum            format: date-time                  oneOf
$defs / $ref within a single file
```

Every rejection returns `(json_pointer, keyword, message)`.

**An unsupported keyword raises `UnsupportedKeyword`. It never silently passes.** A validator
that quietly ignores a constraint is worse than no validator, because it produces confident
green checks over unvalidated data.

Schemas are therefore written to this subset: self-contained, no cross-file `$ref`, and
`additionalProperties: false` at every object level. Constraints the subset cannot express —
`writable_paths ∩ prohibited_paths = ∅`, `risk: critical` forbidding `unsandboxed_tools`,
`base_commit: "unbound"` only when `status: proposed` — are enforced in `validate.py` and
documented in the schema description.

## Consequences

Schema authors are constrained to a small vocabulary, and a future schema needing a keyword
outside it triggers either an implementation extension or a superseding ADR. Both are
deliberate friction.

Adding a pinned validator later requires a superseding ADR naming the exact version and why
the subset proved insufficient. That is carried as open implementation-time validation item 4
from the design review.

## Affected contracts and locks

`schema:agent-artifacts-v1`. Constrains all ten artifact schemas.

## Supersedes

Nothing.

## Required approvals

None beyond G0 scope approval.

## References

- `docs/superpowers/specs/2026-08-31-cashbooks-multi-model-agent-system-design.md` §11.1, §11.2
- `docs/superpowers/reviews/2026-09-01-cashbooks-p0-plan-review.md` §10 item 4
