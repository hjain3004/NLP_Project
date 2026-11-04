# G0 owner approval package

**Status:** proposal data only. Nothing here is an approval, a recorded gate result, or an
architecture-audit artifact. The cloud environment has no seal key, no Mac authentication and no
authority to issue any of it.

## What was evaluated, exactly

`agentctl gate G0 --replay-dry-run agent-system/work/DRY-RUN/evidence/events.json --dry-run-only`
(writes no state) evaluates **11 of 14** criteria: G0-01 … G0-11, all deterministic. All 11 pass on
the PR tree. Its output field `gate_result: "passed"` describes only those 11 and **must not be read
as G0 passing**. Not evaluated and not met:

| Criterion | Needs | State |
|---|---|---|
| G0-12 architecture audit by an independent architectural authority | `agent-system/work/P0/gates/G0-architecture-audit.json`, ingested with `agentctl audit ingest G0 <file>` | **absent.** The `architectural_authority` class is bound to a provider with no adapter here; deciding who may act as that authority is an owner decision. Two independent Opus audits exist as *review inputs* (`reports/final-audit-review-input.md` and later reviews); they are not this artifact. `ingest_audit` only checks self-declared `fresh_session`/`distinct` flags and a construction-log text match. |
| G0-13 scope approval | MAC-sealed `scope_authorization` bound to the scope-statement hash | **absent** |
| G0-14 phase approval | MAC-sealed `phase_authorization` bound to the criteria hash | **absent** |

## Decision 0 — HERO_SCENARIO (decide before approving G0-13)

`docs/product/HERO_SCENARIO.md` says "Level 1 authority" but the approved level-1 set and the
scope statement do not include it. Pick exactly one; both patches were built in a scratch worktree
and validated (`git apply --check` on the PR tree, `agentctl docs verify` ok, dry G0 11/11):

| Option | Patch | Effect | Scope-approval target hash |
|---|---|---|---|
| **B — reclassify as level-3 architecture doc** (smallest) | `option-b-reclassify.patch` (1 line) | No authority-map or scope-statement change | unchanged: `897ab364e170ba0298584fb074cd3eed8a8fdba3c73e7fcf0b28addf1b047cb7` |
| **A — include in level 1** | `option-a-include-level1.patch` | Adds it to `DOCUMENT_AUTHORITY.md` + `document-authority.json` (must agree byte-for-byte, G0-10) and the scope statement (`hero_scenario_sha256`) | `03caa608b216aecd7c40320704ec08c2b0a7169b8392d4e24af574b85a395588` |

Apply the chosen patch with `git apply`, commit, then recompute the targets below.

## Required order of owner actions (Mac, TTY, seal key)

1. **Decision 0** above.
2. **`governance-change-set-v2`** — `schema_commitment` (strong tier: keychain_backed or
   local_authentication). All 10 `before_bytes_sha256` still match the current files.
   Target `P0-GOVERNANCE-CHANGESET-2026-10-11-V2`, digest
   `188264877cb5648faf7e710ccbbbbdad622af3b0f7ea28640c05bbeecdbb00b1`
   (`PYTHONPATH=tools/agentctl python3 -m agentctl hash --json --digest-only agent-system/work/P0/proposals/governance-change-set-v2.json`).
   Then `agentctl approve schema_commitment --target-type governance-change-set --target-id P0-GOVERNANCE-CHANGESET-2026-10-11-V2 --target-hash <digest> --reason … --out …` and `agentctl governance apply --proposal … --approval … [--preflight first]`.
   Its `required_followups` (fixture/test updates: `02-task-contract.json`, the invalid static fixture, `08-human-approval.json`, three test files, `docs/decisions/README.md`) are a separate, reviewed engineering task afterwards; the example-fixture hash updates change G0-04 inputs, so re-run the G0 dry evaluation after them.
3. **ADR-0005 acceptance** — currently `status: proposed` (`docs/decisions/README.md` also says proposed) while `approval-policy.json` cites it. Acceptance is a `consequential_adr_acceptance` approval, an action that only exists once step 2 is applied. There is no registered approval-target mapping for ADRs yet (`approval._APPROVAL_TARGETS` knows only governance change sets), so that mapping plus the docs check in v2's `consumer_requirements` is engineering follow-up work, not something an approval alone unlocks.
4. **G0-12 audit** — decide the architectural authority, run it through the control plane at the final head, ingest it.
5. **G0-13 / G0-14** — `scope_authorization` (target = scope statement hash per Decision 0) and `phase_authorization` (target = criteria hash `c1a199c1aa820932108314834c81246db25a6acb9031a34a5aa2daec673cff1c`, `agentctl hash --json --digest-only agent-system/work/P0/gates/G0-criteria.json`).
6. `agentctl gate G0` at the final head, then the documented post-commit replay checks.

## Superseded / not needed

- `local-authentication-bootstrap-policy.json`: its `before_bytes_sha256` for `approval-principals.json`
  no longer matches the file (later commit `4590ab8` already enabled the path). Do not apply it.
- `G0-criteria-candidate.json` (digest `782034115154b29a1d36bfb460094a751d2fa5d084729b773d42c5a942ba1e42`):
  strengthens G0-03 and the G0-05…09 checks; **optional**, and adopting it changes the criteria hash
  that G0-14 binds. Owner decision; the current criteria are sufficient for the 11 deterministic checks.
- No Git-history reconstruction is required by any binding requirement found in this repair.

## Separate owner decisions raised by the engineering work (not G0 prerequisites)

See `reports/registry-owner-decision.md` (promote / evaluate / rebind the `complex_implementation`
reviewer; candidate-bound reviews are **not merge-eligible** until promoted), plus the open design items
in `CHECKPOINT.md`.
