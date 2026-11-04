> **REVIEW INPUT ONLY.** Produced by a fresh Opus session that authored none of the audited artifacts. It is **not** the G0-12 architecture-audit artifact (`agent-system/work/P0/gates/G0-architecture-audit.json`), was not ingested through `agentctl audit ingest`, carries no control-plane dispatch identity, and counts toward no gate.

AUDIT_PASS_WITH_NONBLOCKING

# Final independent audit: automation delivery (bc7c369..881264f)

I audited 881264f. While I worked, HEAD moved to 182e4f1, which changes only docs (README status, the AUTOMATION_DELIVERY status line, the G0 note). I did no live calls and changed nothing in the repo. The delivery clone's store I queried from a copy in scratch.

## 1. Does the code honor AGENTS.md?

These parts hold up:
- **proposal_only author.** The worker runs `claude -p --tools ""`, with settings and MCP disabled and no `--fallback-model` (adapters/claude_cli.py:424-458). It gets an allowlisted environment, an empty HOME and a working directory outside the repo (claude_cli.py:369-416).
- **The model's output is not trusted as state.** Its proposal is scope-audited before any write (execute.py:277-360). The manifest's bindings, files, commit and usage come from the lease, Git and the provider (execute.py:563-603).
- **Strict result classification.** A result passes only if `is_error` is false, the terminal reason is "completed", the session id the CLI echoes equals the one minted beforehand, and the routed model appears in `modelUsage` (claude_cli.py:625-670). Missing usage is recorded as unavailable, never 0.
- **No silent substitution.** Unsupported providers and missing executables are refused (routing.py:195-219). If the reviewer can't be routed, no lease is issued (routing.py:326-335).
- **Integrator.** It re-derives every binding from the store and Git, including author/reviewer identity disjointness (integrate.py:528-781). It refuses any altered candidate (integrate.py:904-914). It records rejections without advancing the branch, and advances only by compare-and-swap after the gates pass (integrate.py:939-946, 1168-1182).
- **No success after failure.** Every failure ends in a non-success transition (execute.py:829-906). A crash after the compare-and-swap is reconciled, never falsely completed (integrate.py:1062-1105).

The gaps are findings F1 to F4 below.

## 2. Is the delivery claim supported?

Yes, for one Anthropic path and one live sample. I checked:
- **Git.** 89f3bb3 and cb5c731 are ancestors of dccdf89; dccdf89's parents are exactly those two. Both candidates' parent is 2df58e8. Their tree hashes equal the verdicts' `tree_hash` (1fcd104…, 140388a…). 881264f brings in exactly the 4 candidate files.
- **Delivery store** (copy of /home/user/delivery/agent-system/runtime): 57 events. DEMO-101 and DEMO-102 are `completed`; DEMO-002 is `integration_ready`; DEMO-001 is `repair_required`.
- **Identities.** Author and reviewer session and run ids are all distinct UUIDs, and the verdicts record `distinct: true`. The integrator re-checks this at integrate.py:735-764. The served models were claude-sonnet-5 (authors) and claude-opus-5-5 (reviewers).
- **Overlap.** From the run-record timestamps, the two author runs overlap by 145.56 s. The shell timestamps give 145.98 s; they agree.
- **Cost.** The provider-reported costs add up: 0.922973 + 0.505166 = 1.428139; cycle 1 is 1.2325248; the total is 2.660664 USD. Actual platform spend is not observable.

Claims I could not verify, or that are overstated:
- **The integration gate was each candidate's own new test file.** The existing suites were dropped because they fail without `.git` (F2). "Affected checks pass" therefore means the candidate-authored tests passed.
- **The full suite on the integration head failed first (23 failures).** It passed 404/404 only on a re-run in a clean clone. That was an operator diagnostic, not a gate. The 23 failures do match the stated fixture cause (`'dccdf89…' != None`).
- **DEMO-101 is not "low-risk synthetic"** as AUTOMATION_DELIVERY Task 3 specifies. It is risk `medium` and is a real fix to approve.py, the human-approval command, routed as `standard_implementation`. AGENTS §5 assigns security-sensitive code to complex_implementation. The reports call it a "real backlog fix" but do not flag it as a deviation.
- **The candidates reached the session branch by a coordinator `git merge` (881264f),** not through `agentctl integrate`. The commits are unchanged, but the commit message doesn't say this merge happened outside the control plane.
- **Not verified by me:** the event chain hash, the regression run against the unfixed base, the bundle, and "0 manual interventions" (operator-written logs only).
- **CHECKPOINT.md is stale and self-contradictory.** DEMO is shown as "running" and also "blocked", A2 as "blocked on A1", G0-CLASSIFY as "pending", and the next step is out of date.

The registry-profile disclosure is adequate: the README (at 182e4f1), demo-1/2, the CHECKPOINT and the profile's own `demo_profile` note all say it. With the real registry, every standard task is `reviewer_unavailable`.

## 3. Residual risks: are they stated accurately, and do they block "delivered"?

The gate_runner.py:33-48 docstring states them accurately: gates run at the integrator's uid, isolation depends on `unshare`, and there is no OS sandbox. The README states the verify/integrate `.git` mismatch, the missing `repair_required` authorize path and the queue retries.

Two things are understated:
- Candidate code runs **before** independent review, with network access. It can read credential files of the same uid and write to the event store (F1).
- The README doesn't say this.

None of these invalidates the single observed cycle.

What can be claimed: one provider path (Anthropic `claude -p`), observed end to end once, in a clone that used a labelled reviewer profile, with two small tasks, one overlap sample and provider-reported cost.

What cannot be claimed: multi-provider support; use with the real registry; any speedup or schedule; safety on high-risk or adversarial tasks; live evidence of duplicate-request or crash recovery; G0 progress.

## 4. G0

I ran `gate G0 --replay-dry-run … --dry-run-only` at 182e4f1 (rc 0). It wrote nothing: `git status --porcelain --ignored` was identical before and after, there were no new files, and gate.py runs it with `store=None` and `write_result=False`.

| Criteria | Status |
|---|---|
| G0-01 to G0-11 | Pass (dry run, deterministic checks only) |
| G0-12 | Not met: `agent-system/work/P0/gates/G0-architecture-audit.json` does not exist |
| G0-13, G0-14 | Not met: `agent-system/work/P0/approvals/` does not exist |

The dry-run output says `"gate_result":"passed"` even though it evaluated only 11 of the 14 criteria. That label is misleading (pre-existing behaviour).

**This report is a review input, not the recorded architecture-audit artifact, and I did not create one.** G0-12 still needs:
- an `architectural_authority` auditor dispatched by the control plane. That class is bound to openai/gpt-5.6-sol, which has no adapter.
- auditor session and run ids minted by the control plane.
- ingest through `agentctl audit ingest G0 <artifact>`. Note that `ingest_audit` (gate.py:850-886) accepts self-asserted `fresh_session` and `distinct` flags plus a CONSTRUCTION-LOG text match; it does not check the identity by machine.

G0-13 and G0-14 need MAC-sealed human approvals from the owner's Mac, after the HERO_SCENARIO and ADR-0005 decisions.

## 5. Findings

**F1 (non-blocking for this claim; blocking before any untrusted, high-risk or unattended use).** Model-authored code runs at the control-plane uid before review.
- What happens: `verify` runs the candidate's tests in the leased worktree (scheduler.py:1077-1078). The tamper check covers only `.git` metadata, and there is no network namespace (gate_runner.py:19-24).
- Scenario: a candidate test reads credential files by absolute path and sends them out. Or it rewrites the `reviews`/`tasks` rows in agentctl.sqlite3. `_verify` trusts those rows and never checks the event chain, so independent review could be bypassed.
- Also: `verify` does not call `drop_sensitive_environment` (commands/verify.py; integrate does). The host has GH_TOKEN and AWS keys, which matters when `gate_isolation` is "none". Nothing refuses risk high or critical at verify or integrate.
- Minimal fix: run gates under a separate uid with `--net`, and keep the store out of their reach. Cross-check state rows against the event chain in `_verify`. Refuse high-risk tasks at verify and integrate. State this in the README.

**F2 (non-blocking).** Verify and integrate run in different environments.
- What happens: verify runs with a `.git` link; integrate runs in a `.git`-less copy (integrate.py:915-927).
- Scenario: suites that need git can't be integration gates, so the approval suites never gated the approve.py change.
- Minimal fix: give both the same `.git`-less tree, or flag git-dependent checks at plan validation.

**F3 (non-blocking).** Zero checks counts as a pass.
- What happens: `deterministic_checks` has no `minItems` in the schema. `_run_gate_checks` returns `[], None`, so `passed=True` (scheduler.py:1079, 1154-1155). Integrate's only remaining gate is then "candidate-commit-preserved".
- Scenario: a contract with no checks is verified and integrated with nothing run.
- Minimal fix: refuse empty checks at plan registration and in verify.

**F4 (non-blocking).** Superseded tasks re-enter the queue.
- What happens: they are re-tried every time the integration head moves (DEMO-002 at events 54 and 57). A rejected candidate has no terminal state.
- Minimal fix: add an authorised cancel or supersede path. This is an owner decision on the state machine.

**F5 (non-blocking, docs).**
- Update CHECKPOINT.md to match what happened.
- Record the DEMO-101 risk and class deviation from "low-risk synthetic".
- Note that 881264f was a manual merge.
- Relabel the dry-run `gate_result` so it can't read as a pass of all 14 criteria.

## Test suite

I ran `make -C tools/agentctl test` once, at 182e4f1: 404 tests passed, 1 skipped (the opt-in live test), in 224 s. Afterwards the repository's `git status` was clean.
