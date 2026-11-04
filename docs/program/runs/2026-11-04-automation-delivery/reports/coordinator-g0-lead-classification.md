# G0 closeout — lead classification (coordinator)

**Checked against:** commit `3a1457c` (tree identical to main `bc7c369` for all files cited).
**Method:** each lead from the 2026-10-29 handoff was re-checked against current source, not
against the historical ledger (`agent-system/runtime/sdd/p0-finalize-20261018/progress.md` is
git-ignored runtime data and is not present in this clone). Classification is mine; it is not an
audit artifact and does not count toward G0-12.

Current G0 dry evaluation (`agentctl gate G0 --replay-dry-run agent-system/work/DRY-RUN/evidence/events.json --dry-run-only`,
no state written): G0-01..03, 05..11 pass; **G0-04 fails**; G0-12..14 are not evaluable
(no audit artifact, no approvals).

| # | Lead | Still true? | Class | Evidence / required action |
|---|---|---|---|---|
| 1 | **G0-04 fails: valid fixture `agent-system/examples/valid/03-context-manifest.json` pins a stale `AGENTS.md` SHA-256/size** (found by this session, not in the handoff) | Yes. Pin is 14437 bytes / `4ece6621…`; file is now 16639 bytes / `a7e44912…` (AGENTS.md §8 was amended 2026-10-22). Also the baseline `make test` error. | **Blocking** | Mechanical refresh of the fixture's AGENTS.md pin and recomputed manifest hash; no schema or criteria change. Fixture is fragile to any future `AGENTS.md` edit (non-blocking note). |
| 2 | Integrator role says the role "owns selecting the next candidate" | Yes (`agent-system/roles/integrator.md`, Owns bullet 1) | Non-blocking wording | `AUTOMATION_DELIVERY.md` and the Lane B queue make selection deterministic. One-line role fix; level-6 doc, no gate depends on it. |
| 3 | Missing routing fields / command-log refs in schemas & templates | Yes; `governance-change-set-v2` still applies: all 10 `before_bytes_sha256` match the current files. Current digest (`agentctl hash --json --digest-only`): `188264877cb5648faf7e710ccbbbbdad622af3b0f7ea28640c05bbeecdbb00b1`; re-verify at approval time. Reuse v2 unchanged. `agentctl approve` needs the owner's Mac key/TTY. No replacement proposal created. |
| 4 | ADR-0005 acceptance | Yes. `docs/decisions/ADR-0005-symmetric-approval-seal.md` is `status: proposed`; `docs/decisions/README.md:62` says proposed; yet `approval-policy.json` cites it as authority. | **Blocking; needs human** | Consequential ADR acceptance needs a typed approval (v2 `consumer_requirements`). A model may not mark it accepted. |
| 5 | `/var` vs `/private/var` approval-output containment | Reproducible on Linux with any symlinked alias of the repo root: `commands/approve.py::_contained_destination` does `destination.relative_to(root)` on an unresolved path while `root` is resolved. | Blocking (default command fails under aliasing) but self-contained | Code fix + regression test with a symlinked alias dir. Chosen as a real backlog workload for the delivery demo. |
| 6 | G0-03 criterion vs evaluator | Criterion command is only `test ! -d AppleBooks/.git`; evaluator `_nested_git_check` is stronger. | Nonblocking for passing; criteria are immutable | `G0-criteria-candidate.json` exists as the proposal; changing criteria needs human approval of a new criteria hash. |
| 7 | G0-05..09 only check reason-string presence | Partly resolved: `agentctl gate G0` already runs `evidence.verify_dry_run_semantics` before evaluating; the criteria commands themselves remain string checks. | Nonblocking | Criteria are immutable; strengthening them is a criteria-candidate decision for the owner. Semantic replay exists and runs. |
| 8 | HERO_SCENARIO claims "Level 1 authority" | Yes. `docs/DOCUMENT_AUTHORITY.md` level 1 lists only ACTIVE_SCOPE, NON_GOALS, INVARIANTS; `G0-scope-statement.json` hashes those three plus DOCUMENT_AUTHORITY, not HERO_SCENARIO; ACTIVE_SCOPE.md:101 lists it under Related. | **Blocking for G0-13; needs owner decision** | Option A: add HERO_SCENARIO to level 1 + scope statement (changes authority map and scope-statement hash). Option B: reclassify it as a level-3 architecture document (edit its status line only). Either changes the hash the owner approves, so decide before approving G0-13. Not chosen here. |
| 9 | Historical session/run attribution gaps | Not reproducible without the private ledger. | Unsupported by available evidence | Do not infer identities. The actual rule (`review.py` identity comparison, AGENTS.md §5) binds *future* attempts; no rule requires reconstructing history. Owner decision only if the audit demands it. |
| 10 | Archive classification, invalid-fixture taxonomy, AI-07 `unassigned` owner | `INVARIANTS.md` AI-07 owner column reads `unassigned`; archive index current. | Nonblocking | Not a gate criterion; deliberately not made a phase prerequisite. |

## Approval package (owner action; none of these exist yet)

Run on the owner's Mac, from repo root, after the lane work and fixture fix are merged:

```bash
PYTHONPATH=tools/agentctl python3 -m agentctl hash --json --digest-only agent-system/work/P0/proposals/governance-change-set-v2.json
PYTHONPATH=tools/agentctl python3 -m agentctl approve --help   # flags: see commands/approve.py
```

Required, in order: (1) decide HERO_SCENARIO (#8); (2) approve governance-change-set-v2 and
ADR-0005 acceptance; (3) independent architecture audit by a session that authored none of the
audited artifacts (G0-12); (4) G0-13 scope approval and G0-14 phase approval; (5) `agentctl gate G0`.
G0 cannot close from this cloud environment: no seal key, no Mac authentication.
