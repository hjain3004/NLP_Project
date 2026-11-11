> **Authority: Historical (2026-09-01).** CashGuard-era bootstrap sequence, describing a project start that has since happened differently.
> The active authority order is defined in [docs/DOCUMENT_AUTHORITY.md](docs/DOCUMENT_AUTHORITY.md).
> Nothing in this document authorizes work. Treat it as input, not instruction.

# BOOTSTRAP.md — Before the first line of code

This is the only document written for **you** (Himanshu), not for the implementing agent. It covers the things an agent cannot do for you, the exact first-session sequence, and the review protocol that keeps you out of the weeds.

---

## 1. Accounts and installs (do these first — ~45 minutes, $0)

1. **Xcode 26** from the Mac App Store. Open it once, install the iOS 26 simulator runtime when prompted.
2. **Apple Intelligence on your Mac**: System Settings → Apple Intelligence & Siri → turn on, and wait for the model download to finish. Without this, no live-model work happens anywhere — your iPhone 14 Pro can't run it, so the Mac is your only inference host.
3. **Plaid**: sign up at dashboard.plaid.com, stay on Sandbox, copy `client_id` and the **sandbox** secret. Do not request Production access.
4. **ngrok**: free account, install, run `ngrok http 8000` once to confirm it works.
5. **Python 3.12** (`brew install python@3.12`) and confirm `python3.12 --version`.
6. **GitHub repo**: create `cashguard`, private is fine for now — make it public before you put it on a resume.

## 2. The one thing you must build by hand: the Xcode project

Coding agents handle Swift source well but generate `.xcodeproj` files poorly — the format is a fragile, opaque graph, and a corrupted one costs hours. So you create the shell; the agent fills it.

In Xcode: **File → New → Project → Multiplatform → App**. Name `CashGuardApp`, organization identifier `com.<yourname>`, interface SwiftUI, storage **None** (SwiftData gets wired in code), tick "Include Tests". Then **File → New → Package** inside the same workspace, name it `CashGuardKit`, and add it as a local package dependency of the app target. Set both targets' minimum deployment to iOS 26 / macOS 26. Build once (⌘B) and run once (⌘R) so you know the shell is healthy before any agent touches it.

That's your entire manual burden. Everything after this is agent work you review.

## 3. Repo layout before the first agent session

```
cashguard/
├── CLAUDE.md                  ← operating manual (agent reads first)
├── docs/
│   ├── cashguard_prd_v0_2.md  ← product truth
│   ├── TECH_SPEC.md           ← implementation truth
│   ├── DESIGN_SPEC.md         ← visual truth
│   ├── TASKS.md               ← sequence + gates
│   └── INFRA_PLAN.md          ← deployment (Stage A only for now)
├── CashGuardApp/              ← from step 2
├── CashGuardKit/              ← from step 2
└── backend/                   ← agent creates in T0.1
```
`CLAUDE.md` must sit at the repo root — Claude Code loads it automatically as project context.

## 4. Kickoff prompt (paste this to start Phase 0)

> Read `CLAUDE.md`, then `docs/TASKS.md`, then the sections of `docs/TECH_SPEC.md` and `docs/cashguard_prd_v0_2.md` that Phase 0 references. Execute Phase 0 (T0.1) exactly as specified, following the work loop in CLAUDE.md including the docs/ logging. The Xcode project and CashGuardKit package already exist — do not recreate them. Stop when T0.1's gate is green and report: gate output, files created, and any DECISIONS or REVIEW_QUEUE entries you wrote.

For every subsequent phase:

> Continue with Phase N per `docs/TASKS.md`. Re-read `CLAUDE.md` first. Run the phase-end regression loop before you report back.

Run **one phase per session**. Longer sessions drift; phase boundaries are natural review points and keep context clean.

## 5. Your review protocol (~10 minutes per phase)

After each phase, read only these, in this order:
1. `docs/PROGRESS.md` — the tail entry: what shipped, gate output, attempts logged.
2. `docs/REVIEW_QUEUE.md` — anything tagged `must-review` blocks progress; `should-review` you skim; `fyi` you ignore.
3. `docs/DECISIONS.md` — new ADRs. This is where you catch design drift early.
4. From Phase 4 on: `docs/METRICS.md` — recall/precision and the false-positive budget. A regression here matters more than any code detail.
5. `docs/BLOCKERS.md` — if non-empty, that's your actual to-do list.

Then run `make verify` yourself once. If it's green and the queue is clear, start the next phase. You do not need to read the diff.

## 6. Manual checkpoints (things only you can confirm)

These are flagged in TASKS.md gates and cannot be automated: Plaid Link renders and completes in the Simulator (T2.3); the demo business's seeded history looks plausible (T2.2); three golden missions produce sensible answers from the live model (T5.3); the full runbook executes twice without intervention (T6.3); the UI matches DESIGN_SPEC at the largest Dynamic Type size (T6.1). Budget an hour total across the project.

## 7. Learning runway, honestly

You're starting Swift from zero. Phases 0–2 are mostly Python, config, and project plumbing — you can review those now. **Do days 1–15 of Hacking with Swift's 100 Days of SwiftUI before Phase 3**, because Phase 3 is where the pure-Swift engine code lands and reviewing it is the point. Watch the two Foundation Models WWDC25 sessions before Phase 5. If a diff contains something you can't read, that's your next study topic — look it up then, not in advance.

## 8. Order of operations, whole project

Accounts → Xcode shell → docs in place → Phases 0–2 (plumbing) → Swift study → Phases 3–5 (engines, risk, agent) → Phase 6 (UX + demo) → record the demo video → **stop and decide** whether Phases 7–8 and the $99 TestFlight path are worth it based on whether you have real testers waiting. The demo video plus a public repo with metrics is already a complete portfolio artifact.
