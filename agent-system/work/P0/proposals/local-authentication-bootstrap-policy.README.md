# LocalAuthentication bootstrap policy proposal

This is a non-authoritative proposal based on commit `da0418e0044ddd555fe70ace3ba897d57c077f33`.
It makes one policy-data change: the configured `himanshu_jain` principal may use the
already schema-permitted `local_authentication` method. Both live policy files are deliberately
unchanged: `approve_command.requires_key_unlock_passphrase` remains `true`, and the existing
action tiers, including their strong-method alternatives, remain byte-for-byte protected. The
explicit conditional is that this flag requires a passphrase only for the selected
`interactive_tty_with_key_unlock` method; after successful LocalAuthentication, runtime
external-key access is allowed without a raw-key prompt.

The implementation follow-up is limited to the approval command/library and focused tests.
The key-unlock requirement is conditional on the selected method: ordinary interactive TTY
approvals still require it; LocalAuthentication must authenticate the complete binding before
the external seal key is read or used and must not prompt for the raw key passphrase.

The JSON records exact current hashes, the deterministic replacement hash, protected-base
assertions, follow-up scope, and the bounded human authorization text. The identity hash is
defined there as the SHA-256 of canonical JSON with the three self-referential fields omitted.
The proposal remains data until that exact author-level amendment is issued and the normal
control-plane validation, review, and integration gates succeed.
