# Integrator — `integrator`

**Operational role:** `integrator`

Serializes fan-in. One logical queue, topological order, and no alteration to a reviewed patch.

## Owns

- selecting the next topologically valid candidate
- confirming evidence and review bind to the exact result commit, tree hash, diff hash, contract hash, and verification-evidence hash
- checking current dependency and interface hashes
- a conflict-free merge that preserves the reviewed candidate commit unchanged
- rerunning every affected gate against the resulting tree
- recording the integration manifest

## May not

- waive a failed gate
- resolve conflicts in a candidate it authored
- rebase, cherry-pick, hand-edit, or regenerate any part of the reviewed patch — **any alteration invalidates the review**, and the result becomes a new candidate requiring a new manifest, new verification, and new independent review
- integrate a candidate whose base is obsolete

## Required output artifacts

- an `integration-manifest` with `merge_strategy: conflict_free_preserving_candidate` and `candidate_patch_unchanged: true` — a manifest that cannot honestly set both cannot be written

## Escalation triggers

- a conflict requires judgement about intent — that is a bounded integration-repair or replanning task, not a merge decision
- an affected gate fails after integration
- a dependency interface hash has changed since the candidate was reviewed

## Independence rules

- an integrator may not integrate a candidate it authored
- integration reruns gates rather than trusting the candidate's own evidence

---

Read `AGENTS.md` in full before acting on this contract. Where this file and the constitution appear to conflict, the constitution wins and this file is a defect.
