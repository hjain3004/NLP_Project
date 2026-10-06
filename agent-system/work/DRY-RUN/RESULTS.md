# DRY-RUN results

Run date: 2026-10-06

Replay checkout commit: `db2b7cd61862a9f5536b87f11ddfadce5cae7286`

Task-contract base commit: `56c44d4c889e46176cd1b09fb80c8d26f6a7ce22`

Event count: 29

Event-chain hash: `f1a5f3e58fc21bf34beda5bb7ae0a705a8a7cccd44d27ad0220994e91ba59182`

Export file SHA-256: `8e24ea35986b78a1328ca65a14efc534faa9e7c43b6622bd861c3154b881457b`

This is synthetic control-plane evidence. No application source, user document, imported text,
payment instruction, credential, or production database participated in the run.

## Concurrent readiness — G0-05

Command:

```text
python3 -m agentctl plan validate agent-system/work/DRY-RUN/work-package.json
python3 -m agentctl ready --json
```

Observed: all eight synthetic contracts validated. After the conflict leases were expired,
the trusted ready snapshot contained four independent schedulable tasks. The exported chain
contains `reason_code: multiple_ready_tasks` and binds the exact ready task IDs.

## Path-lock conflict — G0-06

Command:

```text
python3 -m agentctl dispatch DRY-005
python3 -m agentctl dispatch DRY-006
python3 -m agentctl ready --json
```

Observed: `DRY-005` acquired `docs/archive/README.md`; `DRY-006`, whose write set was
`docs/archive/**`, exited nonzero with `reason_code: path_lock_conflict`. The ready snapshot
reported the same reason code, proving glob containment rather than string equality.

## Semantic-lock conflict — G0-07

Command:

```text
python3 -m agentctl dispatch DRY-007
python3 -m agentctl dispatch DRY-008
python3 -m agentctl ready --json
```

Observed: `DRY-007` acquired `docs:generated-indexes`. `DRY-008` targeted a different file
but exited nonzero with `reason_code: semantic_lock_conflict` because it requested the same
semantic lock.

## Lease expiry and fencing — G0-06

Command:

```text
python3 -m agentctl dispatch DRY-001
python3 -m agentctl debug expire-lease DRY-001
python3 -m agentctl ingest DRY-001 agent-system/work/DRY-RUN/artifacts/DRY-001-late-manifest.json
python3 -m agentctl status DRY-001 --json
```

Observed: the schema-valid epoch-1 manifest was rejected after forced expiry with
`contract_stale`; status returned `state: ready`, `lease_epoch: 1`, and
`orphan_evidence_count: 1`. The expired submission was retained only as non-integrable orphan
evidence. The export records `reason_code: stale_fencing_token`.

## Valid retry and reviewer independence — G0-08

Command:

```text
python3 -m agentctl dispatch DRY-001
python3 -m agentctl acknowledge DRY-001 f2ba6438c35985500708a35ac5c7eda6ada194601f449a2deb46132863058703
git worktree add --detach agent-system/runtime/worktrees/DRY-001/attempt-2 \
  56c44d4c889e46176cd1b09fb80c8d26f6a7ce22
python3 -m agentctl ingest DRY-001 agent-system/runtime/DRY-001-manifest.json
python3 -m agentctl verify DRY-001
python3 -m agentctl review prepare DRY-001
python3 -m agentctl review assign DRY-001 DRY-001 DRY-001-attempt-2 \
  DRY-001-attempt-2 manual-operator implementation
python3 -m agentctl review assign DRY-001 DRY-001-REVIEW DRY-001-REVIEW-attempt-1 \
  independent-run-DRY-001 independent-session-DRY-001 verifier
python3 -m agentctl review ingest DRY-001 agent-system/runtime/DRY-001-review.json
```

Observed: the epoch-2 worker acknowledged its exact context, committed the synthetic edit in
the exact-base lease worktree, and submitted a manifest matching the resulting Git tree and
binary diff. Trusted deterministic verification produced evidence hash
`b4ed78367bbba5261ec86b87a1870edb41379752a826804945545f0a676eafa6` before the candidate
entered `independent_review`. The trusted assignment command rejected the author identity
with `permission_violation` on `task_id`, before any verdict could self-assert independence.
A distinct reviewer then received a dynamically issued, expiring review lease; a verdict
carrying its exact result, tree, diff, contract, context, and verification bindings was
accepted and the task reached `integration_ready`. The export records
`reason_code: verification_passed`, `reason_code: self_review_rejected`,
`reason_code: independent_reviewer_reserved`, and `reason_code: no_blocking_findings`.

## Context invalidation and recovery — G0-09

Command:

```text
python3 -m agentctl debug bump-interface dry-run:context-v1 \
  agent-system/work/DRY-RUN/interfaces/context-v1-revision-2.json 1
python3 -m agentctl status DRY-002 --json
python3 -m agentctl context rebuild DRY-002
python3 -m agentctl status DRY-002 --json
```

Observed: the pinned interface change moved `DRY-002` to `stale`. Recompilation changed its
context hash from `17df6b1b8943f3dbff53e9d2f21bf004c33470e3d582c7a0b1478a3a34b65f56`
to `8d5554af5fb79d3ee7a2e742d7eb4b42a1ebb0190bc0456f59197e7e3ba727b8`, embedded the
revision-2 source hash `e3b864fc5440ecffabe8e907925230a6388fea921179bdce7454d77e8973c014`,
and returned the task to `validated`. The export records both `context_stale` and
`context_rebuilt`.

## Evidence export — G0-04 through G0-09

Command:

```text
python3 -m agentctl events export --work-package DRY-RUN --out agent-system/work/DRY-RUN/evidence/events.json
python3 -m agentctl hash agent-system/work/DRY-RUN/evidence/events.json
```

Observed: the exporter wrote 29 schema-valid, ordered events. Independent semantic replay
recomputed every chain link and final chain hash, replayed each state transition against the
committed task contracts, verified the candidate/review evidence references, and found all
eight required reasons. The evidence file is committed beside this report; this prose is a
navigation aid, not the authoritative proof.

## Replay notes

This replay ran from a fresh clone of the listed checkout commit using a fresh ignored runtime
database. Reviewer authority came only from the trusted runtime assignment, and interface
invalidation came from the committed revision-2 definition's content hash. The replay made
no control-plane code changes.

## Cleanup

No synthetic edit to an application or program document remains. Only the replay-bound
manifests, exported event chain, and this results record are updated by the evidence commit.
