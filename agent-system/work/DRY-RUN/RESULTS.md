# DRY-RUN results

Run date: 2026-10-05

Replay checkout commit: `5de9a9d135eb970406ede217e167d651cca4de04`

Task-contract base commit: `56c44d4c889e46176cd1b09fb80c8d26f6a7ce22`

Event count: 26

Event-chain hash: `5ea21eb4515908acb0e128edcd307d6fd3ef2eabb8f2ca502d57335993d61bde`

Export file SHA-256: `17068b033399b1578fecb5695f3b45f3f5b16516afcb875555bc742f46293534`

This is synthetic control-plane evidence. No application source, user document, imported text,
payment instruction, credential, or production database participated in the run.

## Concurrent readiness — G0-05

Command:

```text
python3 -m agentctl plan validate agent-system/work/DRY-RUN/work-package.json
python3 -m agentctl ready --json
```

Observed: all eight synthetic contracts validated. The first ready snapshot contained both
`DRY-001` and `DRY-002` (and six additional independent tasks). The exported chain contains
`reason_code: multiple_ready_tasks`.

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
`orphan_evidence_count: 1`. The committed manifest carries the lease ID and fencing token
issued by this replay. The export records `reason_code: stale_fencing_token`.

## Valid retry and reviewer independence — G0-08

Command:

```text
python3 -m agentctl dispatch DRY-001
python3 -m agentctl ingest DRY-001 agent-system/work/DRY-RUN/artifacts/DRY-001-manifest.json
python3 -m agentctl review prepare DRY-001
python3 -m agentctl review assign DRY-001 DRY-001 DRY-001-attempt-2 \
  DRY-001-attempt-2 manual-operator implementation
python3 -m agentctl review assign DRY-001 DRY-001-REVIEW DRY-001-REVIEW-attempt-1 \
  independent-run-DRY-001 independent-session-DRY-001 verifier
python3 -m agentctl review ingest DRY-001 agent-system/runtime/DRY-001-review.json
```

Observed: the epoch-2 candidate entered `independent_review`. The trusted assignment command
rejected the author identity with `permission_violation` on `task_id`, before any verdict
could self-assert independence. A distinct reviewer then received a dynamically issued,
single-use review lease; a verdict carrying that lease was accepted and the task reached
`integration_ready`. The export records `reason_code: self_review_rejected`,
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
to `2ed8c1048ead14e24a7f7eae554803624d0ab3ea2970764b5d875fe61f7f2bc1`, embedded the
revision-2 source hash `e3b864fc5440ecffabe8e907925230a6388fea921179bdce7454d77e8973c014`,
and returned the task to `validated`. The export records both `context_stale` and
`context_rebuilt`.

## Evidence export — G0-04 through G0-09

Command:

```text
python3 -m agentctl events export --work-package DRY-RUN --out agent-system/work/DRY-RUN/evidence/events.json
python3 -m agentctl hash agent-system/work/DRY-RUN/evidence/events.json
```

Observed: the exporter wrote 26 schema-valid, ordered events. Independent replay recomputed
every chain link and the final chain hash, rejected no event, and found all seven required
reason codes. The evidence file is committed beside this report; this prose is a navigation
aid, not the authoritative proof.

## Replay notes

This replay ran from a fresh clone of the listed checkout commit using a fresh ignored runtime
database. Reviewer authority came only from the trusted runtime assignment, and interface
invalidation came from the committed revision-2 definition's content hash. The replay made
no control-plane code changes.

## Cleanup

No synthetic edit to an application or program document remains. Only the replay-bound
manifests, exported event chain, and this results record are updated by the evidence commit.
