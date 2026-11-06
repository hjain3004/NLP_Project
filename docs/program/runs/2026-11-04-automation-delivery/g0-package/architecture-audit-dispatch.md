# G0-12 architecture-audit dispatch and ingestion path

## Status

Preparation only. No authority, session, run, audit verdict, or approval is fabricated.
The registry binds `architectural_authority` to `openai/gpt-5.6-sol`, while the available
adapter bindings contain only Claude. Do not substitute another provider or add an adapter
for this audit.

## Required independent dispatch

Current trusted audit candidate (after the applied governance and follow-ups):

```text
commit: 0c447c51cfa8429bf679f2bfcba9bf17b58e79bb
tree:   8b17ee4d1c21e35e9c1addd1d982000d99e39243
```

If the independent review requires a repair, discard these two values and recompute them
from the reviewed final commit before dispatch.

An authorized operator must create or select the immutable G0 audit task contract at the
final intended commit. The contract must name capability class
`architectural_authority`, the exact audited commit, the G0 criteria, the authority map,
ADR/proposal state, and the G0 artifact schema. The architectural auditor must start in a
fresh session with no prior conversation context and must not be any session listed as an
author of the audited artifacts in `agent-system/work/P0/CONSTRUCTION-LOG.md`.

The existing provider-neutral manual adapter is the valid fallback protocol; it is not a
new provider adapter. With the exact control-plane-produced context pack, task contract,
tool policy, budget, and architecture-audit output schema, the operator stages the pack:

```sh
PYTHONPATH=tools/agentctl python3 - <<'PY'
from agentctl.adapters.base import ManualProposalAdapter
from pathlib import Path
import json

root = Path.cwd()
pack = json.loads((root / "<control-plane-context-pack.json>").read_text())
contract = json.loads((root / "<immutable-audit-task-contract.json>").read_text())
policy = json.loads((root / "<audit-tool-policy.json>").read_text())
budget = json.loads((root / "<audit-budget.json>").read_text())
schema = json.loads((root / "agent-system/schemas/architecture-audit.schema.json").read_text())
adapter = ManualProposalAdapter(root / "agent-system/runtime/manual-dispatch")
result = adapter.run(
    role="architectural_authority",
    context_pack=pack,
    task_contract=contract,
    tool_policy=policy,
    budget=budget,
    output_schema=schema,
)
print(json.dumps(result.__dict__, sort_keys=True))
PY
```

The placeholders are deliberate: they must be replaced by the control plane’s exact,
hash-bound artifacts. Never invent the contract hash, context hash, lease, fencing token,
session ID, or run ID. The fresh architectural-authority session writes only
`returned-artifact.json` beside the staged pack, using the repository’s
`agent-system/templates/architecture-audit.json` shape and the actual final commit/tree
hashes.

## Validation and ingestion

From the trusted final checkout, validate and ingest the returned artifact:

```sh
PYTHONPATH=tools/agentctl python3 -m agentctl validate \
  agent-system/runtime/manual-dispatch/<audit-task-id>/returned-artifact.json
PYTHONPATH=tools/agentctl python3 -m agentctl audit ingest G0 \
  agent-system/runtime/manual-dispatch/<audit-task-id>/returned-artifact.json
```

Ingestion must be run only after the independent auditor returns a complete artifact with
`artifact_type: architecture-audit`, `gate_id: G0`, `audited_commit` equal to the final
trusted head, `verdict: no_blocking_findings`, and truthful identity fields. Existing
review-input files are not substitutes. If ingestion rejects the artifact, preserve the
rejection and repair through the governance workflow; do not edit the audit into passing.

The current `agentctl run` command cannot perform this dispatch because the registered
architectural-authority provider has no adapter. The manual adapter protocol above is the
existing authorized path; no new adapter or provider binding is proposed.

The separate code-review attempt for the ADR target mapping correctly blocked the mapping
because `approval.py` is outside governance-change-set-v2's approved ten-operation set. That
mapping was removed from the active tree; this audit package is unaffected and remains bound
to the current commit/tree above.
