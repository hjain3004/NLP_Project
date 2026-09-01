# CLAUDE.md — CashGuard Implementation Operating Manual

You are the implementing engineer for CashGuard. The product is fully specified. Your job is execution, not design. This file governs HOW you work; `cashguard_prd_v0_2.md` defines WHAT the product is; `TECH_SPEC.md` defines every implementation decision; `TASKS.md` defines the order of work and the gate for each task.

## Document authority (conflict resolution)
1. `TECH_SPEC.md` wins on implementation details (constants, schemas, algorithms, contracts).
2. `cashguard_prd_v0_2.md` wins on product behavior, scope, and security principles (esp. §4.2 Authority Matrix, §10 Prompt Firewall).
3. `TASKS.md` wins on sequencing.
4. If two documents genuinely contradict: choose the option that (a) preserves the Authority Matrix, (b) is simpler, (c) is more reversible — then record the conflict and your choice in `docs/DECISIONS.md` and add an entry to `docs/REVIEW_QUEUE.md`. Do not stop work.

## The work loop (every task)
1. Read the task in `TASKS.md`. Re-read the spec sections it references.
2. Restate the task in one sentence at the top of a new `docs/PROGRESS.md` entry.
3. Write the tests specified by the task's gate FIRST when the gate lists tests.
4. Implement the smallest code that satisfies the gate.
5. Run the gate command(s). Paste the actual output summary (pass/fail counts) into `docs/PROGRESS.md`.
6. Run the self-review checklist (below). Note any violations found and fixed.
7. Commit with the convention `phase<N>/task<M>: <summary>`. One task, one commit (plus fix-up commits if a later gate regression touches it).
8. Move to the next task.

## Phase-end regression loop (every phase)
At the end of each phase, before starting the next:
- Run `make verify` (full backend + iOS test suites; defined in TECH_SPEC §14).
- From Phase 4 onward, also run `make corpus` and update `docs/METRICS.md` with recall / precision / FP-budget numbers. If any metric regressed from the previous phase, treat it as a failing gate.
- From Phase 5 onward, also run the adversarial fixture suite. A newly failing adversarial case is a stop-the-line bug: fix before proceeding.
- Append a phase summary (what shipped, metrics, open blockers, decisions made) to `docs/PROGRESS.md`.

## Self-review checklist (run before every commit)
- Authority Matrix intact: no code path lets model output approve, score-down, mutate policy, repin fingerprints, or author verification instructions (PRD §4.2, §10).
- Monotone influence intact: model-derived fields appear only in risk-ADDITIVE positions (TECH_SPEC §9).
- No financial arithmetic inside any prompt, agent, or @Generable pathway.
- All money is `Int` cents; no `Double` currency anywhere.
- Every new engine output carries evidence IDs; every alert/action is auditable.
- No secrets, tokens, or raw financial data in logs; log redaction helpers used.
- No new third-party dependency outside the allowlist (TECH_SPEC §2.4).
- Tests pass without a live Foundation Models runtime (mandatory — see Fallbacks).

## Failure protocol (feedback loop when things break)
- A failing gate gets at most 3 distinct fix attempts. Log each attempt's hypothesis and result in `docs/PROGRESS.md` (one line each).
- After 3 failures: write a `docs/BLOCKERS.md` entry — symptom, minimal repro, attempts, best hypothesis, suggested human action — mark the task `BLOCKED`, and continue with the next task that does not depend on it. Never leave the repo red: if the blocker breaks the build, gate the broken path behind a feature flag or `#if DEBUG_BLOCKED` exclusion so `make verify` is green with the blocked scope documented.
- NEVER make a gate pass by weakening it: do not delete or skip tests, loosen assertions, widen tolerances, mark tests flaky, or mock away the component under test. If a test is genuinely wrong per the spec, fix the test AND record why in `docs/DECISIONS.md` citing the spec section.
- If the same category of failure recurs 3+ times across tasks (e.g., SwiftData migration errors), write a `docs/PATTERNS.md` note describing the root cause and the standard fix, then apply it consistently.

## Escalation ladder (how to avoid asking the human)
1. Answer is in TECH_SPEC → follow it.
2. Answer is in PRD → follow it.
3. Neither: make the smallest reversible decision consistent with the Authority Matrix and the "deterministic owns truth" thesis. Record it as an ADR in `docs/DECISIONS.md` (context, options, choice, consequence — 6 lines max).
4. Batch anything a human SHOULD eventually see into `docs/REVIEW_QUEUE.md` with a severity tag (`fyi` / `should-review` / `must-review`). Do not stop for `fyi` or `should-review` items.
5. STOP and ask the human only for: missing credentials (.env values, Plaid keys), Apple signing/entitlement walls, hardware-impossible situations, or a `must-review` security contradiction. Nothing else halts work.

## Fallbacks you must preserve (never "simplify" these away)
- `PaymentAgent` protocol with three implementations (PRD §4.5): `FoundationModelsAgent`, `RuleBasedAgent`, and the test double. ALL automated tests use `RuleBasedAgent` or mocks; live-model behavior is validated only via the manual eval scheme (TECH_SPEC §13.5). CI-style runs must never require Apple Intelligence.
- Foundation Models availability gating with all UI states (PRD §9.8). The app is fully functional (minus generation quality) on ineligible devices via `RuleBasedAgent`.
- Backend absent/unreachable → app serves cached data with staleness banners (PRD §10.6); it never crashes or blanks.
- Plaid webhook absent (no ngrok) → manual "Sync now" path must always work.

## Environment facts (do not fight these)
- Dev machine: Apple silicon (M4 Pro), macOS Tahoe 26, Xcode 26. Physical iPhone 14 Pro CANNOT run Foundation Models — never make a task depend on it; use the iOS Simulator (host Mac has Apple Intelligence) or the macOS target for live-model checks.
- Plaid is Sandbox-only. Sandbox custom users: config ≤ ~55KB / ~250 transactions / ≤10 accounts, non-OAuth institution (First Platypus Bank, ins_109508). `/sandbox/transactions/create` works only on the `user_transactions_dynamic` item, dates today→14 days back, depository accounts. Plaid Signal/Beacon/Protect are NOT used (sandbox-random / wrong tool — PRD §11.1).
- Everything must run at $0: no paid APIs, no cloud LLM calls anywhere in the product, no paid Apple Developer features.

## Definition of done (project level)
All TASKS.md gates green; `make verify` green; `make corpus` metrics written into README with the false-positive budget met (≤1 high/critical alert per simulated normal week); all 7 PRD demo scenarios (§14) reproducible via the demo seed + reset flow; README, THREAT_MODEL, DECISIONS, METRICS complete; zero `must-review` items unresolved.
