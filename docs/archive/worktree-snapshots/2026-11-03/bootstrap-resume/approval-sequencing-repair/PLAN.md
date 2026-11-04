# Approval-sequencing repair implementation plan (proposed)

Status: proposal only. The existing patch `003d3515…39c44` is a partial implementation: it covers prebinding/output ordering but does not propose the outer idempotency correction or recovery CLI branch below. No source has been applied.

## Frozen interface and source evidence

Add opt-in `agentctl approve --recover-output` with the existing required issuance parser fields `action`, `--target-type`, `--target-id`, `--target-hash`, `--reason`/`--scope`, plus `--approval-id` (canonical lower-case UUID) and `--out PATH`. `--recover-output` requires `--approval-id`; it copies one already registered, verified record and never authenticates to issue, seals, refreshes, registers, consumes, revokes, or replaces an approval. `--auth-method` remains an issuance request only and is rejected with `--recover-output` rather than relaxing the configured method.

This reuses—not extends—the existing library retrieval interface: `ApprovalManager.verify_registered` (`approval.py:519-545`) reads the full sealed JSON from `artifacts.payload_json` for `artifact_type = 'human-approval'` and returns `verify_approval(...)`. `verify_approval` (`approval.py:349-463`) validates schema, MAC, action, target type/ID/hash, scope, configured principal/method, single-use, expiry, revocation, and consumption, then returns the full record. Recovery passes the exact request `{approval_id, expected_action, expected_target_type, expected_target_id, expected_target_hash, expected_scope}` to that method using the controller's trusted internal verification key; no key is surfaced to CLI output. It must additionally require the returned record's `approval_id` to equal the requested ID.

The only record source is that verified `artifacts.payload_json`; recovery must never reconstruct from a file or partial metadata, reseal, or write an event. It creates `--out` only by exclusive, no-follow creation after path containment validation. Existing paths, symlinks, path escapes, and all parent-directory/permission/I/O errors fail without overwriting or creating authority. Success publishes the verified full JSON and a structured `recovered_registered_approval` status. Any post-registration issuance mirror I/O failure returns structured `approval_issued_output_failed` with approval ID, exact target hash, and the frozen recovery invocation; it must not log record contents, PII, or keys. Existing `ApprovalRejected` handling and CLI nonzero failure convention remain authoritative; no new generic export framework or policy error taxonomy is proposed.

Synthetic example only (never an immutable task value):

```text
agentctl approve schema_commitment --target-type task-contract --target-id P0-FWD-001 \
  --target-hash aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --scope 'Authorize schema_commitment for task-contract P0-FWD-001@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' \
  --reason synthetic-recovery-test --approval-id 11111111-1111-4111-8111-111111111111 \
  --recover-output --out agent-system/work/P0/approvals/synthetic-recovered.json
```

## Single bounded task

Write set: `tools/agentctl/agentctl/approval.py`, `tools/agentctl/agentctl/commands/approve.py`, and new `tools/agentctl/tests/test_approval_prebind.py`. Read set: those files, `store.py`, `local_authentication.py`, `scheduler.py`, `validate.py`, approval policy/principal policy, task schema, and existing approval tests. Semantic lock: `schema:agent-artifacts-v1` (the registered machine-artifact meaning/validation lock). No schema, policy, AGENTS, routing, Swift, protected-test, or historical replay change.

1. Write synthetic CLI tests with temporary EventStore, synthetic key, and injected native-auth adapter. Assert prebind happens before authentication; invalid IDs fail before auth; preflight opens no EventStore/DB; default no-ID CLI behavior remains unchanged.
2. Exercise the real `ApprovalManager.register → EventStore.execute` boundary, not a direct mutation: concurrently register two distinct, valid sealed payloads with one approval ID and distinct nonces/output paths. Registration idempotency/event identity must include canonical candidate payload hash so exactly one persists; the distinct loser raises `approval_id_collision`, writes no mirror, and cannot receive cached success. Re-registering the byte-identical payload retains legitimate idempotent/historical replay. Assert winner `artifacts.payload_json`, registration event, and later consumption bytes are unchanged.
3. Test every exact binding and eligibility guard through recovery: MAC tamper; wrong action/target type/ID/hash/scope; wrong principal/method; expired, revoked, and consumed records; unknown ID. Each fails closed and does not issue/register/consume/reseal.
4. Inject post-registration `FileExistsError`, `PermissionError`, missing-parent/OSError, and crash. Assert record/event remain authoritative, no ID reuse/reissue occurs, later recovery copies the same verified ID only, and later dispatch still atomically rechecks/consumes it so recovery cannot replay authority.
5. Implement only the prebinding/collision/idempotency branch and frozen recovery CLI branch. Run after implementation only:

```text
PYTHONPATH=tools/agentctl python3 -m unittest tools/agentctl/tests/test_approval_prebind.py
PYTHONPATH=tools/agentctl python3 -m unittest tools/agentctl/tests/test_approval.py
```

6. Independent verifier checks the three-path scope, preflight non-write evidence, and exact-versus-distinct registration replay behavior. A genuinely provider-distinct reviewer is a prerequisite and is not claimed available. Trusted integration reruns the two commands from the exact base.

## Authorization and control-plane handling

This is code-only and has no `human_approval_ref`: schema and scheduler permit that absence, so no consumed approval is reused and no hash-cycle workaround is claimed. Controller validation, isolated worktree creation, lease/context binding, implementation manifest ingestion, reviewer identity, and integration remain future control-plane actions. The task may write only the three files; the implementation manifest is controller-owned and must echo actual—not fabricated—lease/context/fencing data.
