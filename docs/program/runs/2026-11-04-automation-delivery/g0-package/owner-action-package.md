# Ordered G0 owner-action package

This package is preparation, not approval. All commands below are concrete; commands that
issue, apply, ingest, or close state remain owner- or authority-gated.

## 1. Apply HERO_SCENARIO Option B

The patch is already prepared and validated with `git apply --check`:

```sh
git apply docs/program/runs/2026-11-04-automation-delivery/g0-package/option-b-reclassify.patch
git diff --check
PYTHONPATH=tools/agentctl python3 -m agentctl docs verify
git add docs/product/HERO_SCENARIO.md
git commit -m "docs: classify HERO_SCENARIO as level-3 architecture"
```

This changes one status line only. It leaves the scope-approval target at
`897ab364e170ba0298584fb074cd3eed8a8fdba3c73e7fcf0b28addf1b047cb7`. Do not run these
commands in the preparation checkout until the owner authorizes application.

## 2. Revalidate governance-change-set-v2

Current validation result: `status=ok`, `authoritative=false`, `operation_count=10`.
The exact proposal digest is
`188264877cb5648faf7e710ccbbbbdad622af3b0f7ea28640c05bbeecdbb00b1`.

```sh
PYTHONPATH=tools/agentctl python3 - <<'PY'
from agentctl.proposals import validate_governance_change_set
from agentctl.hashing import sha256_of_json
from pathlib import Path
import json
p = Path("agent-system/work/P0/proposals/governance-change-set-v2.json")
print(validate_governance_change_set(p, repository_root=Path.cwd()))
print(sha256_of_json(json.loads(p.read_text())))
PY
```

Effects are exactly: require three routing fields in task contracts; require `log_ref` on
manifest commands; make work-package `reason` conditional for unassigned/canceled status;
add `consequential_adr_acceptance`; require strong authentication for that action; update
four templates; teach `validate.py` to consume the action. Required fixture, test, and
decisions-index follow-ups remain listed in the proposal.

The existing approval command is:

```sh
PYTHONPATH=tools/agentctl python3 -m agentctl approve schema_commitment \
  --target-type governance-change-set \
  --target-id P0-GOVERNANCE-CHANGESET-2026-10-11-V2 \
  --target-hash 188264877cb5648faf7e710ccbbbbdad622af3b0f7ea28640c05bbeecdbb00b1 \
  --reason "Approve exactly governance-change-set-v2" \
  --out agent-system/work/P0/approvals/governance-change-set-v2-approval.json
PYTHONPATH=tools/agentctl python3 -m agentctl governance apply \
  --proposal agent-system/work/P0/proposals/governance-change-set-v2.json \
  --approval agent-system/work/P0/approvals/governance-change-set-v2-approval.json \
  --preflight
```

The first command requires the configured human authentication/seal path. The second must
not be run until that sealed artifact exists and independent review approves the follow-up.

## 3. Review and accept ADR-0005

Use `adr-0005-acceptance-followup.md` for the exact mapping and tests. Current ADR byte hash:
`672940b94ea9becca0aeb9d1fa92ff2405054f367bf2bccb2052f9e8d11a09bd`.
The mapping cannot be activated before governance v2 because the schema and policy action do
not yet exist in the live tree. Acceptance must be a separate sealed
`consequential_adr_acceptance`; do not change the ADR status during preparation.

## 4. Obtain and ingest G0-12

Use `architecture-audit-dispatch.md`. The current final PR head is
`4ed3c3d812725ac85315eeda3ac6cf155c3ba79c`; if Option B or any follow-up changes are
applied, replace it with the final trusted head and recompute its tree hash. The authority
must be independent and use the existing manual protocol or an already authorized provider
path—never a fabricated artifact or a new adapter.

## 5. Issue G0 approvals after the final tree is fixed

```sh
PYTHONPATH=tools/agentctl python3 -m agentctl hash --json --digest-only \
  agent-system/work/P0/gates/G0-scope-statement.json
# Option B expected target: 897ab364e170ba0298584fb074cd3eed8a8fdba3c73e7fcf0b28addf1b047cb7
PYTHONPATH=tools/agentctl python3 -m agentctl hash --json --digest-only \
  agent-system/work/P0/gates/G0-criteria.json
# Current criteria target: c1a199c1aa820932108314834c81246db25a6acb9031a34a5aa2daec673cff1c
```

Issue `scope_authorization` against the final scope hash and `phase_authorization` against
the criteria hash using the existing `agentctl approve` command. These are the G0-13 and
G0-14 approvals, not substitutes for ADR-0005 or governance approval.

## 6. Close only after all evidence exists

```sh
PYTHONPATH=tools/agentctl python3 -m agentctl gate G0
```

The gate must produce a result bound to the final commit with G0-12, G0-13, and G0-14
resolved. Until then, G0 remains open.

## Governing approval citations

- `AGENTS.md` §10: typed approvals are always required for schema commitments and phase-gate
  closure; silence or a chat instruction is not approval.
- `AGENTS.md` §5 separation-of-duty invariant 6: a model-authored ADR remains proposed until
  the declared authority accepts it.
- `docs/decisions/README.md` lines 10–18 and 37–38: only the declared authority accepts an
  ADR, and consequential/schema ADRs require a typed approval artifact reference.
- `agent-system/work/P0/gates/G0-criteria.json` G0-12–G0-14: architecture audit, scope
  authorization, and phase authorization are independent gate evidence.

No additional typed approval is claimed for the wording-only supervised-workflow summary;
it remains proposal data and does not alter active scope.
