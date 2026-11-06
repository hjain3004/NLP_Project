# Fixture pin refresh: stale AGENTS.md pin (G0-04)

Branch: `claude/fix-fixture-pin`. Worktree: `/home/user/wt/fixture`.

## Reproduction (before change)

Command: `PYTHONPATH=tools/agentctl python3 -m agentctl validate agent-system/examples/valid/03-context-manifest.json` (exit 1)

```
agentctl: artifact_hash_mismatch: context input hash or byte count does not match (actual_bytes=16639, actual_hash='a7e449125ce93abe56195e93fafd514bb65674394dcf47fa0526c6723601e7c2', expected_bytes=14437, expected_hash='4ece66211ea6b2fa6c9c8e2a22e4979bccfac3c5d7417082fb0f9222d69f4b25', path='AGENTS.md')
```

## Before / after values

| File | Field | Before | After |
|---|---|---|---|
| `03-context-manifest.json` | `inputs.authority_excerpt.sha256` | `4ece66211ea6b2fa6c9c8e2a22e4979bccfac3c5d7417082fb0f9222d69f4b25` | `a7e449125ce93abe56195e93fafd514bb65674394dcf47fa0526c6723601e7c2` |
| `03-context-manifest.json` | `inputs.authority_excerpt.bytes` | `14437` | `16639` |
| `03-context-manifest.json` | `context_hash` | `d356a0559c5d1d5fd3d5b1ba2d0b70981041302c854cf742662bc6b9a7a74b79` | `c4f0cb5aa423cb0811150add4768dfe6313ad687bbebd5f2b622c1e1e75e9e13` |
| `04-implementation-manifest.json` | `context_hash` | `d356a0559c5d1d5fd3d5b1ba2d0b70981041302c854cf742662bc6b9a7a74b79` | `c4f0cb5aa423cb0811150add4768dfe6313ad687bbebd5f2b622c1e1e75e9e13` |
| `05-review-verdict.json` | `context_hash` | `d356a0559c5d1d5fd3d5b1ba2d0b70981041302c854cf742662bc6b9a7a74b79` | `c4f0cb5aa423cb0811150add4768dfe6313ad687bbebd5f2b622c1e1e75e9e13` |

The new `context_hash` is `sha256_of_json_excluding(manifest, "context_hash")` from `tools/agentctl/agentctl/hashing.py`. The stored pre-change hash was checked against the same rule and matched, so the method is confirmed.

## Commands and results (after change)

- `validate` on every file in `agent-system/examples/valid/`: all pass.
- `validate` on `agent-system/examples/invalid/static/*.json`: all rejected for their intended reasons (missing `escalation`, overlapping write/prohibit paths, path traversal, `secret_access` true, attempts over ceiling, `patch_altered`). Their fixtures were not touched.
- `python3 -m unittest discover -s tools/agentctl/tests -t tools/agentctl`: Ran 232 tests, OK.
- `PYTHONPATH=tools/agentctl python3 -m agentctl gate G0 --replay-dry-run agent-system/work/DRY-RUN/evidence/events.json --dry-run-only`: G0-01 through G0-11 all pass (G0-04 was failing before).

## Baseline for comparison

On HEAD before this change: full suite Ran 232, errors=1; G0-04 fail.

After the 03-only change (before 04/05 were updated): errors=3, because 04 and 05 and the retry test still carried the old `context_hash`. This is why 04 and 05 were updated in the same change.

## Not changed

`AGENTS.md`, tests, schemas, and `invalid/` fixtures were not modified.
