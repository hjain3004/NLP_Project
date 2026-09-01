# ADR-0005 — Symmetric approval seal at Level 1

## ID and title

ADR-0005 — Symmetric approval seal at Level 1

## Status

status: accepted

## Context

Human authorization in this system is a typed record bound to an exact target hash, with a
scope, an expiry, and single-use semantics. Phase gates, invariant changes, protected-test
changes, destructive operations, and public claims all depend on one. The design describes it
as *signed*, and separation-of-duty invariant 8 requires that a model cannot create, refresh,
broaden, or revoke one.

Python's standard library provides `hmac` and `hashlib`. **It provides no asymmetric signing
primitive** — no Ed25519, no RSA. ADR-0002 commits to standard library first.

The plan review found this: the word "signed" appeared throughout while the stated toolchain
could not sign, leaving an implementer to invent something silently. It also found that no
task provisioned a key at all, so whoever first ran `agentctl approve` would generate one —
and the agent executing the plan is the one running commands.

## Considered options

**Pin a cryptography dependency and use Ed25519.** True asymmetric signing: the verification
key can be public while the signing key stays private. Rejected for Level 1: it introduces a
dependency ahead of ADR-0003's threshold, and it does not fix the real weakness, which is that
the key lives on the same machine as everything else.

**A Boolean or a string.** Rejected outright. That is what the design explicitly forbids: an
approval a caller can assert is not an approval.

**HMAC-SHA256 over the canonical record**, with the key held outside the repository.

## Decision

`control_plane_seal = HMAC-SHA256(key, canonical_json(approval minus seal, consumed_at, revoked_at))`, hex-encoded.

The field is named **`control_plane_seal`**, not `control_plane_signature`, because it is a
keyed MAC, **not a digital signature**, and the name should not overstate the property.

Key handling:

- 32 random bytes at a configured absolute path outside the repository, mode `0600`;
- **provisioned by the human, before any approval path runs** (P0 Task 17 Step 1);
- fingerprint recorded in `agent-system/policies/approval-principals.json`, committed; the key
  itself never enters the repository;
- `agentctl doctor` fails if the path resolves inside any worktree, if the mode is wider than
  `0600`, if the fingerprint is unrecorded, or if the key's mtime post-dates the first
  `agentctl` commit.

`agentctl approve` refuses a non-TTY stdin and, for the
`interactive_tty_with_key_unlock` tier, requires the passphrase that unlocks the key. A seal
produced without a successful unlock is refused. `fallback_to_weaker_method` is `false`: an
action whose policy tier has no implemented method is **hard-refused**, never downgraded.

## Consequences

**Honest security property: this prevents accidental and in-worktree forgery. It does not
protect against an attacker who can read the key file.** Anyone with local read access to the
key can mint approvals. That is stated here rather than discovered later, and it is why the
key lives outside every worktree and why `doctor` refuses to start otherwise.

`isatty()` is not identity — an agent running through a pty satisfies a bare TTY check
trivially. The key-unlock requirement is what makes the weak tier mean something, which
matters because the two approvals G0 actually needs sit in it.

Upgrading to asymmetric signing requires a superseding ADR naming a pinned dependency. Carried
as open item 8 in the plan review.

## Affected contracts and locks

`schema:agent-artifacts-v1` (`human-approval.schema.json`),
`policy:agent-constitution-v1` (`AGENTS.md` §10).

## Supersedes

The unqualified use of "signature" in
`docs/superpowers/specs/2026-08-31-cashbooks-multi-model-agent-system-design.md` §9.1.1, for
Level 1 implementations.

## Required approvals

Human approval, as an invariant-adjacent security decision. Recorded at G0.

## References

- `docs/superpowers/reviews/2026-09-01-cashbooks-p0-plan-review.md` C-08
- `docs/superpowers/plans/2026-09-01-cashbooks-p0-control-plane-bootstrap.md` Task 17
