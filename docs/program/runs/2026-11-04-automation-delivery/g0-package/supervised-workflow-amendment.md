# Supervised workflow amendment — proposal only

## Decision

Defer unfinished unattended provider execution and automatic candidate collection. Enable
development work through the already implemented `ManualProposalAdapter` protocol until an
authenticated provider adapter produces a complete live cycle.

## Existing machinery

The proposed workflow is exactly:

`agentctl dispatch` → immutable dispatch pack → typed `returned-artifact.json` →
`agentctl ingest` → verifier-owned checks → independent `agentctl review` →
`agentctl integrate`.

The adapter has no shell or repository access. The candidate remains bound to its task,
attempt, context, contract, lease, and fencing token. No candidate binding is promoted and
no automatic-provider claim is made. A manual artifact is simulated/supervised evidence,
not live provider evidence.

## Exact change set

The exact text-only additions proposed for `docs/program/ROADMAP.md` are:

```text
### Supervised development workflow (proposed amendment; pending owner approval)

Until unattended provider execution has a valid authenticated adapter and a recorded live
cycle, development tasks may proceed only through the existing `ManualProposalAdapter`
protocol: dispatch the immutable task/context pack, return the typed implementation
manifest, run verifier-owned checks, obtain an independent review, and integrate only the
unchanged verified candidate. This is a development workflow, not a product capability,
does not promote a routing candidate, and does not close G0. It introduces no new control
plane, schema, approval bypass, provider binding, or scope expansion.
```

The exact additions proposed for `docs/program/AUTOMATION_DELIVERY.md` are:

```text
**Proposed status amendment:** Unattended provider execution and automatic candidate
collection remain deferred after the recorded authentication refusal. The supported interim
development path is the existing `ManualProposalAdapter` dispatch/return protocol followed
by verifier-owned checks, independent review, and serialized integration. This amendment is
proposal-only until the owner accepts the associated program-document change; it does not
alter G0 criteria, active product scope, routing bindings, approval policy, or invariants.

The supervised interim path above is limited to manually returned, hash-bound artifacts;
it is not evidence of unattended provider integration.
```

It is deliberately not applied in this closeout because the user requested preparation only.
No separate typed approval is claimed for this wording-only summary amendment. The roadmap
itself states that `ACTIVE_SCOPE.md` and `DOCUMENT_AUTHORITY.md` govern if its summary is
incomplete; any actual scope, schema, invariant, ADR, or phase-gate change remains subject
to its own governing approval requirement.

The change set does not modify:

- G0 criteria or their hash;
- `ACTIVE_SCOPE.md`, invariants, document authority, routing bindings, schemas, or approval policy;
- protected tests, the event store, or the control plane;
- Swift, UI, Foundation Models, or any product implementation.

## Acceptance and evidence

Before activation, an independent reviewer must review the patch as a documentation/program
change. After the owner chooses the HERO_SCENARIO option and supplies the required typed
approvals, the active-document hashes must be recomputed and the actual G0 gate rerun.
Nothing in this proposal satisfies G0-12, G0-13, or G0-14.

## Current limitation

The registry's `architectural_authority` binding is `openai/gpt-5.6-sol`, but no adapter is
available for that binding in this environment. The existing architecture review inputs are
not identity-bound G0 audit artifacts and must not be relabeled as one.
