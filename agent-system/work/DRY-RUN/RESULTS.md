# DRY-RUN results

Run date: 2026-09-27

Base commit: `56c44d4c889e46176cd1b09fb80c8d26f6a7ce22`

Event count: 25

Event-chain hash: `b143f48383070c3d07140832a1750ce3a13267e4b4b5fa7f3559a359277d1d1d`

Export file SHA-256: `6cb1c26f2653a16223a91056448246b07ea148702574c1277958eb0903df25d2`

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
`orphan_evidence_count: 1`. The export records `reason_code: stale_fencing_token`.

## Valid retry and reviewer independence — G0-08

Command:

```text
python3 -m agentctl dispatch DRY-001
python3 -m agentctl ingest DRY-001 agent-system/work/DRY-RUN/artifacts/DRY-001-manifest.json
python3 -m agentctl review prepare DRY-001
python3 -m agentctl review ingest DRY-001 agent-system/work/DRY-RUN/artifacts/DRY-001-self-review.json
python3 -m agentctl review ingest DRY-001 agent-system/work/DRY-RUN/artifacts/DRY-001-review.json
```

Observed: the epoch-2 candidate entered `independent_review`. The verdict sharing the
author's run and session identities was rejected with `permission_violation` on
`provider_run_id`, despite claiming `distinct: true`. A separately identified verdict was
accepted and the task reached `integration_ready`. The export records
`reason_code: self_review_rejected`.

## Context invalidation and recovery — G0-09

Command:

```text
python3 -m agentctl debug bump-interface contract:core-domain-v1
python3 -m agentctl status DRY-002 --json
python3 -m agentctl context rebuild DRY-002
python3 -m agentctl status DRY-002 --json
```

Observed: the pinned interface change moved `DRY-002` to `stale`. Recompilation changed its
context hash from `b19f4ac5c6acbe2983824a514fa0c68af347623bb1bdad42c8c2503701c356c2`
to `0c1dc58ff7502d284fbdfbb66c9a9f150ad387375ce962df959805b52064bb2e`
and returned the task to `validated`. The export records both `context_stale` and
`context_rebuilt`.

## Evidence export — G0-04 through G0-09

Command:

```text
python3 -m agentctl events export --work-package DRY-RUN --out agent-system/work/DRY-RUN/evidence/events.json
python3 -m agentctl hash agent-system/work/DRY-RUN/evidence/events.json
```

Observed: the exporter wrote 25 schema-valid, ordered events. Independent replay recomputed
every chain link and the final chain hash, rejected no event, and found all seven required
reason codes. The evidence file is committed beside this report; this prose is a navigation
aid, not the authoritative proof.

## Bootstrap corrections found during execution

The initial work-package status used `active`, which is not permitted by the work-package
schema; it was corrected to `in_progress`. Plan validation also originally embedded the
current time in context manifests, making a second validation produce different task
identity. Compilation is now deterministic from the contract's creation time, and a test
executes plan validation twice against one store and requires identical results.

The retry reuses the identical compiled context by hash. Attempt identity remains enforced
by the active lease and the submitted implementation manifest; context linkage is based on
the immutable context hash so an unchanged pack does not need to be relabelled on retry.

## Cleanup

The one synthetic line added to `agent-system/examples/README.md` was removed after the
evidence export. No synthetic edit to an application or program document remains.
