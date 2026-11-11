# ADR-0002 — Python development CLI beside a Swift product

## ID and title

ADR-0002 — Python development CLI beside a Swift product

## Status

status: superseded by ADR-0006

## Context

The control plane needs process control, Git integration, SQLite access, JSON artifact
handling, and eventually provider adapters. CashBooks itself is a Swift product. Introducing a
second language into a solo project is a real cost: another toolchain, another set of idioms,
another thing to keep current.

## Considered options

**Write `agentctl` in Swift.** One language across the repository. Rejected: orchestration
work — subprocess management, SQLite, JSON schema validation, HTTP adapters — is
substantially more code in Swift, and building the tool would compete for attention with
building the product. It would also add build weight to a development toolchain that needs to
start fast and be edited constantly.

**Shell scripts.** Rejected: the control plane maintains transactional state across twelve
SQLite tables with fencing tokens and hash binding. That is not a shell program.

**Python 3.12+, standard library first.** Portable process control, `sqlite3`, `json`,
`pathlib`, `hashlib`, and `dataclasses` in the box.

## Decision

`agentctl` is a Python CLI, targeting 3.12 or later, standard library first.

It is **development-only**. It is never linked into, imported by, or shipped with the
CashBooks application. It never processes a real user document; its test PDFs are synthetic
repository fixtures.

A third-party dependency requires a superseding ADR naming the exact pinned version and why a
standard-library implementation would be fragile.

## Consequences

Two languages in one repository, and a contributor must have a current Python to run the
control plane. The 3.12 floor is real — `datetime.UTC` and modern typing are used — and the
system Python on macOS is typically older, so the toolchain check in P0 Task 1 fails loudly
rather than producing confusing errors later.

Against that: orchestration cost drops substantially, and the application's architecture stays
clean because none of this machinery is inside it.

## Affected contracts and locks

`schema:agent-artifacts-v1`. Establishes `tools/agentctl/` and the invocation contract
(`PYTHONPATH` at the absolute `tools/agentctl`, `make -C tools/agentctl test` as the single
canonical test invocation).

## Supersedes

Nothing.

## Required approvals

None beyond G0 scope approval. No product behaviour is affected.

## References

- `docs/superpowers/specs/2026-08-31-cashbooks-multi-model-agent-system-design.md` §11.1, §55.3
- `docs/superpowers/plans/2026-09-01-cashbooks-p0-control-plane-bootstrap.md` Tasks 9, 12–17
