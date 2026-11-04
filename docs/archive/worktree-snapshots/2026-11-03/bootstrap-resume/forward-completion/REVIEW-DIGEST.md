# Reviewer digest — P0-FWD proposal

Scope of this review package: two typed, non-authoritative proposal artifacts and this provenance record. No canonical work package, task contract, approval, event, runtime state, target file, test, gate, or product source was modified.

| Item | Proposed binding |
| --- | --- |
| Work package | `P0-FWD`, status `proposed`, plan source raw SHA-256 `ef128d6efedcb61618559d8adc115e554aa9fece7aa0384e5cfefa2474084b4c` |
| Task | `P0-FWD-001`, status `proposed`, base `e4dbba9c76ee495e28cc1c403c6513011065749a`, canonical contract hash `bf629ee90800904a7284d4543434666531d654963ac48b113f3cdec8e404f` |
| Governance source | v2 canonical `188264877cb5648faf7e710ccbbbbdad622af3b0f7ea28640c05bbeecdbb00b1`; raw `24110dc9ad9aa55cf190a46c2a58455b19ef3d25fd7a8621dcf78e74bc5eae31` |
| Intended target delta | ten target files only, diff SHA-256 `bffc580350dc7c8375dfd1e1bed821a1c691aad6d840b4a8fe87b008a16ce602` |
| Fresh approval preflight | completed with `local_authentication` and exact task-contract scope; no prompt, write, seal, registration, or consumption |

Blocking findings for independent review:

1. The existing v2 approval is a different, expired/consumed governance-change-set approval; it cannot authorize the capture task.
2. Fresh task-level approval is required by the schema-commitment invariant, but the current controller creates a hash cycle: issuance needs the final contract hash, while scheduler dispatch requires the newly issued approval ID inside that contract. No real immutable reference can be supplied without changing the binding.
3. The current lease controller records a relative isolated-worktree path but does not create a Git worktree at the task base commit.
4. The requested `required_provider_diversity: true` has no known non-OpenAI reviewer route in the registry and no scheduler enforcement. An OpenAI Luna reviewer is not provider-distinct from Terra.

This package is deliberately not self-validated, registered, dispatched, or asserted ready. It is submitted for independent review and a bounded control-plane decision on the blockers.
