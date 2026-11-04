# P0 forward-capture proposal — non-authoritative

This directory is a planning artifact only. It neither registers `P0-FWD-001`, creates a lease or a worktree, issues or consumes approval, applies governance, nor closes G0.

## Fixed source bindings

The candidate base is `e4dbba9c76ee495e28cc1c403c6513011065749a`. The proposal source is `P0-GOVERNANCE-CHANGESET-2026-10-11-V2`: canonical JSON hash `188264877cb5648faf7e710ccbbbbdad622af3b0f7ea28640c05bbeecdbb00b1`; raw-byte SHA-256 `24110dc9ad9aa55cf190a46c2a58455b19ef3d25fd7a8621dcf78e74bc5eae31`. The ten approved target-file diff SHA-256 is `bffc580350dc7c8375dfd1e1bed821a1c691aad6d840b4a8fe87b008a16ce602`.

`P0-FWD.work-package.proposed.json` uses `ef128d6efedcb61618559d8adc115e554aa9fece7aa0384e5cfefa2474084b4c` as `plan_hash` because it is the raw-byte SHA-256 of the documented active phase plan `docs/superpowers/plans/2026-09-01-cashbooks-p0-control-plane-bootstrap.md` at the source commit that materialized existing P0 and DRY-RUN work-package records. It is not a self-hash or a copied placeholder.

The task contract is a typed approval-target draft. Its `contract_hash` is canonical SHA-256 of the object with only `identity.contract_hash` omitted, as specified in `agent-system/README.md`; its raw-byte and canonical-file hashes are recorded in the review digest after independent review. Its planned expiry is a 24-hour proposal-review window from the supplied UTC clock, not a lease or authorization.

## Approval result: fresh approval is required, but cannot be sequenced

The eventual task writes four schemas and the approval policy, so the constitution's schema-commitment rule requires a new approval. The existing v2 record is unusable: it authorizes only the governance-change-set target and its operations, is expired at the supplied clock, and was reported consumed. It must remain unchanged.

The controller supports a task-level approval binding in `Scheduler._approval_request` (`tools/agentctl/agentctl/scheduler.py:247-261`): its target is `task-contract/P0-FWD-001/<final contract hash>` and its exact scope is `Authorize schema_commitment for task-contract P0-FWD-001@<final contract hash>`. However, the issued approval ID must be stored in `permissions.human_approval_ref`; storing it changes the contract hash to which that approval must bind. No existing command resolves this hash cycle. Therefore this proposal deliberately has no fabricated approval ID and must not be promoted, validated as executable, registered, or dispatched.

The following non-writing input preflight targets the exact proposed contract hash and a distinct future approval path. It only proves that the existing CLI accepts the action, strong local-authentication method, syntax, and output location; it does not resolve, issue, seal, register, or consume a task-contract target:

```bash
PYTHONPATH=tools/agentctl python3 -m agentctl approve schema_commitment --target-type task-contract --target-id P0-FWD-001 --target-hash bf629ee90800904a7284d4543434666535531d654963ac48b113f3cdec8e404f --scope 'Authorize schema_commitment for task-contract P0-FWD-001@bf629ee90800904a7284d4543434666531d654963ac48b113f3cdec8e404f' --reason 'Authorize the exact P0-FWD-001 schema-commitment capture contract' --auth-method local_authentication --preflight --out agent-system/work/P0/approvals/P0-FWD-001-schema_commitment-approval.json
```

The executed preflight command with the computed contract hash and its output are recorded in the review digest. It is not authorization.

## Controller facts and required later procedure

`PlanLoader.validate` can validate a canonical work-package path, compile a context with `ContextCompiler`, and register the task (`tools/agentctl/agentctl/plan.py:58-149`). The compiled context hash is runtime-derived after canonical placement; this proposal does not invent one. `Scheduler.dispatch` performs admission and then consumes a registered approval only when the task has a real approval reference (`scheduler.py:247-273, 411-466`).

`LeaseManager.issue` records `agent-system/runtime/worktrees/P0-FWD-001/attempt-<epoch>` but does not create a Git worktree (`tools/agentctl/agentctl/leases.py:124-127`). An actual isolated worktree creator is absent. The controller therefore cannot yet perform the requested clean isolated attempt.

Any later implementation must first obtain a human-approved resolution for the approval-reference hash cycle and the absent worktree materialization capability. It must then use different recorded author and reviewer provider-run/session identities. The registry has the requested Terra binding only for `complex_implementation`; it supplies no known non-OpenAI reviewer capability, and the runtime scheduler does not enforce `required_provider_diversity`. Luna is also OpenAI and is not a provider-distinct substitute. These are blockers, not waivable checklist items.

If those controls exist later, the allowed order is: independently validate this proposal; install a final canonical contract/work-package through an authorized control-plane change; compile and record the actual context; issue and atomically consume a fresh registered approval under the approved sequencing mechanism; create a clean worktree at the bound base; lease and acknowledge; execute only the contract's write set; independently verify; then integrate unchanged. Fixture/context refresh remains a separate later proposal after the candidate commit.

## Candidate command and output guard

The only permitted Git read commands are `git rev-parse`, `git status`, `git diff --check`, `git diff --name-only`, and the exact `git diff --no-ext-diff --binary` hash command in the task. The only Git write command is `git add`/`git commit` for the declared eleven paths in the controller-created isolated worktree. No `reset`, `clean`, `checkout`, `restore`, `rebase`, `merge`, `stash`, or governance/approval command is permitted. The evidence file and implementation manifest are candidate outputs only; no proposal, approval, task-registration, gate, test, product, or documentation path may change.
