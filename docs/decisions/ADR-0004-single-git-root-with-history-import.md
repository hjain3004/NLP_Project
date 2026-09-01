# ADR-0004 — Single Git root with history import

## ID and title

ADR-0004 — Single Git root with history import

## Status

status: accepted

## Context

Before P0, `/Users/himanshu_jain/cashguard` was not a Git repository. It held the design
documents, the research corpus, and a nested repository at `cashguard/AppleBooks` containing
the generated Xcode project at commit `441a6f8 "Initial Commit"`.

The control plane binds task contracts to exact base commits and creates per-attempt
worktrees. Both require one repository root containing everything a task can touch. A nested
repository inside an unversioned parent gives neither.

## Considered options

**Leave the nesting and version only `AppleBooks/`.** Rejected: the documents that carry all
the authority — invariants, contracts, schemas — would be outside version control, so no task
contract could bind them to a commit.

**Add `AppleBooks` as a submodule.** Rejected: submodules make per-attempt worktrees and
atomic cross-boundary commits substantially harder, for no benefit here — there is one
developer and one product, not a shared library.

**`git init` at the root and start fresh, abandoning the Xcode history.** Rejected: one commit
is a small loss, but discarding history because it is inconvenient sets a precedent in a
project whose entire thesis is evidence and traceability.

**`git init` at the root, then import the nested history under its existing prefix** via
`merge -s ours --no-commit --allow-unrelated-histories` followed by
`read-tree --prefix=AppleBooks/ -u`.

## Decision

Consolidate to one root with a history import.

The original commit is preserved as the second parent of the import commit and remains
reachable. A verifiable bundle (`git bundle create --all`) is written to a fixed path outside
the repository before anything moves, and retained until G0 closes.

The proof that no application code changed is **tree-hash equality**:

```text
git rev-parse <NESTED_HEAD>^{tree}  ==  git rev-parse HEAD:AppleBooks
```

Not a pathspec diff. Verified empirically on a fixture reproducing the layout:
`git diff <NESTED_HEAD> HEAD -- AppleBooks/` reports a three-file difference on a **correct**
migration, because the imported commit's tree root is the project while `HEAD` nests it one
level down.

Renaming `AppleBooks` to `CashBooks` is **P1-A**, not part of this decision. The import
preserves the existing prefix.

## Consequences

The repository has a merge commit at its base with two unrelated histories, which is unusual
but correct and self-documenting. Anyone reading `git log --graph` sees exactly what happened.

Executed 2026-09-01: root commit `43ae709`, import `6a20332`, evidence `73ba609`. Subtree hash
`a8a08f8f928233100790ba84d73b8c550491eace` on both sides.

An interrupted `merge --no-commit` leaves `MERGE_HEAD`, which the next commit from any later
task silently absorbs — producing a two-parent commit with the wrong message and a missing
subtree, while the reachability check still passes. Every task after the migration therefore
guards on `.git/MERGE_HEAD`.

## Affected contracts and locks

`repo:path-layout`.

## Supersedes

The migration procedure in `docs/superpowers/plans/2026-08-31-cashbooks-control-plane.md`
Task 1, whose `git bundle verify` step could not run from a non-repository directory.

## Required approvals

None beyond G0 scope approval. No application code changed, proven by tree hash.

## References

- `docs/superpowers/plans/2026-09-01-cashbooks-p0-control-plane-bootstrap.md` Task 2
- `agent-system/work/P0/evidence/migration.json`
- `docs/superpowers/reviews/2026-09-01-cashbooks-p0-plan-review.md` §2, C-01, C-03, C-04
