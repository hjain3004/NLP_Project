# Architecture Decision Records

**Status:** Active index.
**Written:** P0 Task 5, 2026-09-01.

An ADR records one decision, why it was made, what was rejected, and what it costs. Accepted
ADRs sit at **level 2** in `docs/DOCUMENT_AUTHORITY.md` — above active architecture documents
and the canonical design, below human-approved scope and machine-enforced policy.

## Who may accept one

**Agents may propose ADRs. Only the declared authority accepts them.** A model-authored ADR
remains `proposed` until accepted — that is separation-of-duty invariant 6 in `AGENTS.md`, and
it is why `Status` is a required field rather than an assumption.

An ADR that changes an invariant, a schema commitment, or anything with a
`release_or_public_claim` consequence requires human approval, recorded as a typed approval
artifact and referenced under `Required approvals`.

## Required headings

Every ADR uses exactly these, in this order:

```text
ID and title
Status
Context
Considered options
Decision
Consequences
Affected contracts and locks
Supersedes
Required approvals
References
```

A check enforces their presence, recognizes explicit lifecycle statuses, and rejects an
accepted consequential ADR that lacks a typed approval-artifact reference.

## Status values

- `proposed` — written, not yet accepted. Non-authoritative.
- `accepted` — governs. Level 2.
- `superseded` — replaced. Names its replacement under `Supersedes` in the new record and
  keeps its own text unchanged, because an ADR's value is partly the record of what was
  believed at the time.
- `rejected` — considered and declined. Kept, so the same option is not re-litigated silently.

## Numbering

Sequential, zero-padded, never reused: `ADR-0001-short-slug.md`. A superseded ADR keeps its
number.

## Current records

| ID | Title | Status |
|---|---|---|
| ADR-0001 | Deterministic multi-model control plane | accepted |
| ADR-0002 | Python development CLI beside a Swift product | accepted |
| ADR-0003 | Bounded standard-library schema validation | accepted |
| ADR-0004 | Single Git root with history import | accepted |
| ADR-0005 | Symmetric approval seal at Level 1 | proposed |
