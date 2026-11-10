# ADR-0005 acceptance mapping and follow-up — proposal only

## Why this is required

`docs/decisions/ADR-0005-symmetric-approval-seal.md` remains `status: proposed`.
`docs/decisions/README.md` states that agents may propose ADRs but only the declared
authority accepts them, and that an ADR changing a schema commitment requires a typed human
approval artifact under `Required approvals`. The constitution repeats this in separation
of duty 6 and AGENTS.md §10. The governance proposal adds the missing
`consequential_adr_acceptance` action and binds it to `keychain_backed` or
`local_authentication`.

## Exact target mapping to prepare after governance approval

The existing resolver only registers JSON governance proposals. It must gain a dedicated,
fixed ADR target mapping (not caller-selected path discovery):

This mapping is intentionally not active in the current tree. An independent review found
that adding it to `approval.py` is outside governance-change-set-v2's ten approved operation
paths. It is therefore a separate engineering proposal requiring its own write set,
independent review, and ordinary integration before any ADR approval can be issued.

```text
target_type: adr
target_id: ADR-0005
path: docs/decisions/ADR-0005-symmetric-approval-seal.md
target_hash: SHA-256 of the exact UTF-8 file bytes
```

Current target hash: `672940b94ea9becca0aeb9d1fa92ff2405054f367bf2bccb2052f9e8d11a09bd`.

The resolver follow-up must:

1. register only `adr/ADR-0005` in the fixed target table;
2. verify the file is inside the repository, regular, and non-symlinked;
3. parse the exact `ID and title` and `Status` headings, requiring `ADR-0005` and
   `status: proposed` before approval;
4. bind approval verification to the exact file-byte hash; and
5. reject any other ADR identifier or a non-proposed/changed record.

Do not change ADR-0005 to `accepted` as part of this follow-up. Acceptance is the owner’s
authenticated action and must consume a sealed approval with:

```text
authorized_action: consequential_adr_acceptance
target_type: adr
target_id: ADR-0005
target_hash: 672940b94ea9becca0aeb9d1fa92ff2405054f367bf2bccb2052f9e8d11a09bd
scope: Accept ADR-0005 exactly as hashed; no other ADR or policy change
```

## Independent-review write set

The follow-up implementation must be independently reviewed across these files:

- `tools/agentctl/agentctl/approval.py` — fixed ADR mapping and byte-hash resolver;
- `tools/agentctl/tests/test_approval.py` — valid binding, changed-file rejection,
  wrong-ID rejection, and non-proposed-status rejection;
- `agent-system/schemas/human-approval.schema.json` and
  `agent-system/policies/approval-policy.json` — only after the approved governance
  change-set has applied them;
- `docs/decisions/README.md` — document the typed consequential-ADR requirement;
- `docs/decisions/ADR-0005-symmetric-approval-seal.md` — status changes only in the
  owner-authorized acceptance commit, never in the preparation patch.

The existing governance proposal’s `required_followups` already names the schema, policy,
validation, fixture, test, and decisions-index work. This file adds the missing fixed ADR
target mapping and its acceptance-specific tests; it is not itself an approval or an
authoritative policy change.
