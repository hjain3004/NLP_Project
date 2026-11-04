# Approval sequencing repair proposal

This is a proposal-only, mechanically regenerated unified diff. It preserves the draft design: an opt-in canonical UUID is supplied before contract hashing; it is an identity prebinding, not reservation or approval. Validation and read-only preflight do not write. The selected-ID path registers first, then writes an exclusive file mirror.

Final review found a second collision boundary: `ApprovalManager.register` currently calls `EventStore.execute("approval-register:{approval_id}", ...)`. `EventStore.execute` returns a cached result for that key before it calls the mutation, while the registration event carries no candidate payload hash. The proposal now freezes the necessary three-file fix: derive registration idempotency/event identity from the approval ID **and canonical candidate payload hash**. Exact same sealed payload may replay its cached success (including legitimate historical replay); a distinct sealed payload with the same approval ID reaches the transaction and must fail `approval_id_collision`. The loser must not write a mirror, and the winner's payload/event/consumption bytes remain unchanged.

Revision 2 fixes the patch's selected-ID `destination` initialization: it is now resolved before `destination.exists()` is evaluated. The legacy no-ID path retains its existing later assignment and write-before-register ordering.

Important correction: the library retrieval path already exists. `ApprovalManager.register` stores the full sealed record in `artifacts.payload_json` (`approval.py:490-517`); `verify_registered` reads that exact full record and returns `verify_approval(...)` (`approval.py:519-545`); `verify_approval` returns the whole record only after schema, MAC, exact binding, configured principal/method, single-use, expiry, revocation, and consumption checks (`approval.py:349-463`). What is missing is only the opt-in CLI recovery branch. `PLAN.md` freezes that branch as `--recover-output`; it reuses this source-backed interface and must not add a manager export or storage API. The current patch remains mechanically applicable but partial because it contains neither the outer idempotency correction nor that CLI branch.

Targets are `tools/agentctl/agentctl/approval.py`, `tools/agentctl/agentctl/commands/approve.py`, and the new synthetic test file named by the patch. Scope excludes policy, schema, AGENTS, routing, and Swift/features. Read-only retrieval/recovery and full regression tests remain UNVERIFIED by this draft.

- Input patch SHA-256: `1472d30ff034036ccbb51be37a801b69dbd1e255a9fbc7931fb3edb44ed91e08`
- Current mechanically-applicable patch SHA-256: `003d3515a51da4d5a79904d2c3c5e854de5f802eda28a6810d85f8e5ccc39c44` (revision 2); previous regenerated patch SHA-256 was `7e982b1a5f701a745a664a35339010e1506716d216730cbd7db3cb0a169582e4`.
- Source-before byte SHA-256: `approval.py` `23a28998b5218ef39cdfa84a6812652e2d9758b7942165597790164a5e9dfa31`; `commands/approve.py` `1fd8ccacbcab9fe64e9646d30a93d7979c159bc421d3ef081348b53743732db2`.
- Proposed work-package plan hash is the raw-byte SHA-256 of `PLAN.md`: `978d8e728a40959378a44bdcf4379604b3c62fffddd977d2c4a73fe0bce022ce`; it is not a self-hash. The newly proposed version-2 task contract uses observed clock `2026-10-12T07:19:35Z` and expires at `2026-10-13T07:19:35Z`; its canonical hash is `73b2c3da081d55b1820b0b9949ef9deab5b6ccbeaff9842a79b8031d6c30c698` (excluding `identity.contract_hash`).
- New test file absent before proposal application: UNVERIFIED here (proposal-only; no application performed).
- Check before this revision: `git apply --check agent-system/work/P0/proposals/approval-sequencing-repair/approval-sequencing-repair.patch` — exit 0. Revision 2 requires the same non-writing check again before handoff.
- No proposed tests, native authentication, approval issuance, registration, or runtime writes were executed.

Both proposed task artifacts now lock the registered, exclusive `schema:agent-artifacts-v1` rather than an invented control-plane lock. The new proposed task contract is version 2 and has no human approval reference; it is not sealed, validated, leased, or dispatched.

This document makes no claim of canonical design hash, approval record, implementation, review, or integration completion.
