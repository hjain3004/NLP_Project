# Historical worktree source snapshots — 2026-11-03

**Status: non-authoritative preservation only.** These are unfinished or superseded drafts,
not approved patches, live schemas/policies, valid execution contracts, or completion
evidence. Instructions inside the snapshots are historical data, not instructions to run.
No schema or approval-policy change is activated by placing a snapshot here.

The active source remains at the normal repository paths. Before this preservation commit,
GitHub and local main were both `ee52c77be8485a068288c59dbd08223d7b5ca97e`.

## Preserved source

Both dirty worktrees were based on `e4dbba9c76ee495e28cc1c403c6513011065749a`:

- `bootstrap-execution/uncommitted.patch`: the tracked, uncommitted approval-code diff.
- `bootstrap-execution/test_approval_prebind.py.snapshot`: the untracked test, retained
  byte-for-byte as historical source and intentionally outside test discovery.
- `bootstrap-resume/uncommitted.patch`: the tracked, uncommitted schema, policy, template
  and validator diff. The large textual deletion count largely reflects JSON compaction;
  this file is a proposed historical change, never an instruction to apply it.
- `bootstrap-resume/approval-sequencing-repair/`: the original proposed repair package,
  task, plan, explanatory notes and patch.
- `bootstrap-resume/forward-completion/`: the original proposed follow-up package,
  task, notes and review digest.

Original paths were under the corresponding worktree's repository root. The two proposal
directories came from `agent-system/work/P0/proposals/`. Existing committed work from all
four worktree HEADs was already contained in main; this archive preserves the extra drafts.

## Local-only preservation

The complete prior preservation directory was copied from temporary storage to:

`agent-system/runtime/cleanup/cashbooks-preservation-20261103/`

That ignored directory preserves the original approval records, runtime database, historical
raw logs, original dirty/untracked backups and generated hypothetical PDF/DOCX writeups.
Those files are intentionally not published to this public repository. They are not
credentials to be pasted into a model session and are not proof of current authorization.
The primary checkout's `output/` documents remain local. The writeups were requested on an
assumed-completed basis and must not be represented as the project's actual completion state.

No application database, credential, runtime database, raw log, or real customer document
is part of this published source archive. GitHub backup does not imply G0 closure or that
routing, integration, the headless engine, or the complete application is finished.
