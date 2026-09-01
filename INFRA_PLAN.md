# CashGuard Infrastructure Plan — from laptop to App Store

Written for an infra beginner. Three stages. Each stage is complete on its own; do not start a stage until the previous one is genuinely needed. The governing principle: **boring infrastructure, sized to reality.** CashGuard's backend is one small stateless-ish API plus one small database. Nothing about it needs Kubernetes, service meshes, or multi-region anything.

Verified facts used below (July 2026): Apple's `container` tool hit 1.0.0 on June 9, 2026 — it runs Linux containers as per-container lightweight VMs on Apple silicon under macOS 26, consumes/produces standard OCI images and Dockerfiles, but has no Docker-API/Compose compatibility and is a **local dev runtime, not a deployment platform**. Xcode Cloud includes 25 free compute hours/month with the $99/yr Apple Developer Program membership. TestFlight supports internal testing (up to 100 members) without App Review and external testing (up to 10,000 testers) after a lighter beta review.

---

## Stage A — Now: local dev + CI (cost: $0)

What you're shipping: a demo on your Mac + a green CI badge. This is all the portfolio needs.

**A1. Backend runtime.** Run FastAPI directly (`uvicorn`) for daily dev. Add a `Dockerfile` anyway (python:3.12-slim, non-root user, `uvicorn app.main:app`) — not because you need containers today, but because Stage B deploys this exact image, and CI builds it from day one.

**A2. Where Apple `container` actually fits.** Use it as your local container runtime instead of Docker Desktop: `container build -t cashguard-backend .` and `container run -p 8000:8000 cashguard-backend` to prove the image works. That's the whole role. It will NOT: ship your iOS app, host your backend for customers, or run Compose stacks. If you later need multi-service local orchestration (Postgres + API + worker), that's Docker Desktop/OrbStack territory — but CashGuard doesn't need that; SQLite runs in-process.
- Caveats to know: macOS 26 + Apple silicon only (you're fine); community reports of slow unpacking for huge images (yours is small); builder networking quirks existed pre-1.0 — if `apt/apk` fails during build, check the release notes before debugging your Dockerfile.

**A3. Webhooks.** ngrok free tier gives the public URL Plaid needs in dev. The URL changes each restart; `make backend-run` should print the current one and you paste it into the Plaid dashboard webhook field (or pass it in `/link/token/create`). Annoying, acceptable, free.

**A4. CI (GitHub Actions, free for public repos).** Two workflows:
- `backend.yml`: ubuntu-latest → ruff + pytest on every push/PR.
- `ios.yml`: macos-15 (or newest macos-26 image when available) → `xcodebuild test -scheme CashGuardKit -destination 'platform=macOS'` + `make app-build`. Kit tests need no Foundation Models runtime by design, so this runs headless.
- Optional third job: `make corpus` with the metrics summary posted as a job artifact — your fraud metrics become CI-enforced.

**A5. Secrets discipline from day one.** `.env` git-ignored; GitHub Actions secrets for anything CI needs; never a key in code or in the iOS bundle. This habit is 80% of "fintech infra maturity" at your scale.

---

## Stage B — TestFlight beta (cost: $99/yr Apple + ~$0–10/mo hosting)

What you're shipping: real people (classmates, Maly Tech colleagues, the 5 SMB owners) installing CashGuard on their phones, still against **Plaid Sandbox / demo data**. This is the highest stage worth reaching before graduation, and it's what turns the resume bullet from "built" to "shipped a beta to N users."

**B1. Apple Developer Program ($99/yr).** Enroll as an individual. This unlocks: App Store Connect, TestFlight, Xcode Cloud free tier, push entitlements. Create the app record + bundle ID; let Xcode manage signing automatically (do not hand-manage certificates in 2026).

**B2. iOS CI/CD: Xcode Cloud, not fastlane.** In Xcode: Product → Xcode Cloud → create workflow: on push to `main` → build → run tests → archive → distribute to TestFlight internal group. It manages signing/notarization itself, and 25 free compute hours ≈ 100+ builds/month at your project size. fastlane is the right tool at a company with custom pipelines; for a solo dev it's maintenance burden with no payoff.

**B3. TestFlight rollout.** Internal group (you + a couple of accounts) gets every build instantly. External group (your SMB testers) after Apple's beta review — expect the review to ask what the app does with financial data; your answer is "demo/sandbox data only, no real bank credentials in beta," and the in-app SIMULATED labels (PRD §13.7 lineage) matter here.

**B4. Backend hosting: one container on a PaaS.** Pick ONE of Render / Railway / Fly.io — all deploy a Dockerfile from a GitHub repo with automatic HTTPS and env-var secrets; all land in the $0–10/mo range for one tiny instance (free allowances change frequently — check current pricing pages the week you deploy, don't trust blog posts). Selection heuristic for a beginner: **Render** if you want the least concepts (connect repo → it builds → it runs), Fly.io if you want to feel closer to the metal. Set: `PLAID_CLIENT_ID/SECRET/ENV=sandbox`, `CG_DEV_TOKEN`, via the platform's secrets UI.

**B5. Database: move SQLite → managed Postgres when you deploy.** SQLite on PaaS ephemeral filesystems loses data on redeploy. Neon or Supabase give a free managed Postgres; SQLAlchemy makes the swap a connection-string change plus one migration (add Alembic at this point, single initial migration). Keep SQLite for local dev/tests via the same SQLAlchemy models.

**B6. Webhooks become boring.** Your PaaS URL is stable: set `https://<app>.onrender.com/api/webhook/plaid` in Plaid once. Delete ngrok from your life.

**B7. Observability, minimum viable.** PaaS log tail + a `/healthz` endpoint + UptimeRobot free ping. Skip Sentry/Datadog — they contradict the privacy story and you don't have the traffic to need them. Your existing redaction rules now really matter: platform logs are off-device.

**B8. What does NOT change.** The on-device architecture is untouched — engines, agent, SwiftData all live on the phone. The backend stays a dumb Plaid pipe. This is the payoff of the local-first design: "scaling" the backend is scaling a proxy, not the product.

---

## Stage C — Real customers, real bank data (do NOT attempt solo as a student — read to understand the cliff)

Everything before this line is engineering. This line is where fintech becomes a regulated-adjacent business:

1. **Plaid Production access.** Application + approval, pay-as-you-go per-Item pricing, and a security questionnaire about how you store and protect data. Real money: at micro-SMB price points, Plaid per-user costs are a meaningful fraction of any plausible subscription.
2. **App Review for a finance app** handling real bank connections gets materially more scrutiny: privacy policy, data-handling disclosures, support URL, and Apple's privacy nutrition labels must all be accurate and consistent with what the binary does.
3. **Legal surface**: terms of service, privacy policy (GDPR/CCPA questions arrive immediately), a business entity to sign Plaid's production agreement, and liability thinking around fraud alerts (what happens when your tripwire misses one — or blocks a legitimate payroll payment?).
4. **Operations**: backups, an incident-response note, key rotation, a support inbox, and someone on call — i.e., a team.

The honest strategic read: Stage C is a company, not a capstone. The resume value asymptote is reached at Stage B. If CashGuard ever deserves Stage C, it deserves co-founders and a design-partner customer first.

---

## Explicitly rejected options (so you can defend the choices)
- **Kubernetes**: one container with zero scaling requirements. K8s here is renting a container ship to deliver one pizza. Learn K8s later, on a project that has the problem K8s solves.
- **AWS/GCP raw (EC2, ECS, Cloud Run + VPCs + IAM)**: maximal learning-curve tax for zero product benefit at this scale. A PaaS teaches you the deployment concepts (images, env secrets, health checks, logs) that transfer anyway.
- **apple/container as production runtime**: it's a macOS dev tool; your Mac is not a server.
- **Serverless (Lambda) for the backend**: Plaid sync is stateful-ish (cursors, DB) and long-polling webhooks are simpler on an always-on tiny instance; cold-start debugging is a poor use of your time.
- **Firebase/Supabase-as-backend**: hides exactly the FastAPI/Plaid engineering you're trying to demonstrate.

## Order of operations checklist
1. [Stage A] Dockerfile + CI green + ngrok webhook flow (weekend).
2. Build the product (TASKS.md phases).
3. [Stage B] Enroll ADP → Xcode Cloud workflow → TestFlight internal (one day).
4. Deploy backend to PaaS + Postgres + stable webhook (one day).
5. External TestFlight group → your 5 SMB validators install it themselves (this is the resume moment).
6. Stop. Stage C only ever happens with co-founders and a real design partner.
