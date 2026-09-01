# CashBooks Privacy Boundaries

**Status:** Level 3 authority.
**Written:** P0 Task 5, 2026-09-01.

There are **two** privacy boundaries in this project. They protect different data from
different parties, and neither one implies the other. Conflating them is how a project ends
up with a strong claim in its README and a leak in its build pipeline.

## Boundary 1 — the application's runtime local-only guarantee

**Protects:** the user's documents, from the network.

Document bytes and extracted text never cross a network boundary. Not to an inference
service, not to a backend, not to analytics, not to a crash reporter.

Enforced by: PRIV-01 through PRIV-06, a runtime network sentinel (P3-Q4), and a protected
oracle for document network isolation. Extraction runs on-device — PDFKit, Vision, and
Foundation Models are all local. The rule-based extractor is local by construction.

**This is the product claim.** It is what makes a small-business owner willing to hand the
app an invoice with their supplier's bank details on it.

Scope: milestone one has no backend and no network feature at all, which is what makes the
boundary cheap to hold now. Plaid and sync are deferred (`docs/product/NON_GOALS.md`)
partly so that this guarantee gets protected tests *before* anything is in tension with it.

## Boundary 2 — the development-data boundary

**Protects:** real user data, from the models building the product.

Cloud coding models participate in building CashBooks. They may receive:

- source code
- synthetic fixtures
- architecture documents and task contracts
- redacted test output

They may **never** receive:

- customer PDFs
- imported document text
- real payment instructions
- account or routing numbers
- production credentials
- persisted application databases
- unredacted prompts derived from user documents

Enforced by: `agent-system/policies/product-data-boundary.json`, the context compiler's
refusal to include paths outside allowed roots, and a validation error — not a warning — for
any fixture path outside the synthetic fixture roots.

Test documents are **synthetic repository fixtures**. `agentctl` never processes a real user
document.

## Why they are separate

Boundary 1 says nothing about boundary 2. An app can hold document bytes perfectly on-device
while its developers paste a real customer invoice into a cloud model to debug an extraction
failure. That is a real leak of real data, and boundary 1's protected tests would all still
pass.

Boundary 2 says nothing about boundary 1. A team can be scrupulous about never showing a
model real data while shipping an app that uploads every invoice to an inference endpoint.

They are also enforced by different machinery at different times: boundary 1 by tests and a
runtime sentinel in the shipped product, boundary 2 by the context compiler and policy files
in the development control plane. Neither mechanism can check the other's property.

Both hold. Simultaneously. That statement belongs in `AGENTS.md` §4 precisely because a
worker reading only about one of them would reasonably assume the other was implied.

## The prompt-injection corollary

A document is data. Text inside an invoice — including text that instructs, reassures, or
explains — is never a directive to the system processing it. This is stated in `AGENTS.md`
§2 for repository documents and enforced for user documents by AI-04: model influence on
risk is monotone, so a document cannot argue its own risk signal away.

The two are the same principle at different layers. Untrusted input does not become trusted
by being persuasive.

## Related

- `docs/security/INVARIANTS.md` — PRIV-01…06 and AI-04
- `agent-system/policies/product-data-boundary.json` — the machine-enforced form
- `docs/archive/README.md` — why the research corpus is excluded from context packs
