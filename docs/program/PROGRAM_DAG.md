# CashBooks Program DAG

**Status:** Active topology for the authorized P0–P3 program.
**Machine source:** `agent-system/work/program-dag.json`.

Phase gates authorize the next phase. Dependency edges determine scheduling within a phase.
An arrow means the node on the right consumes the integrated result on the left.

## P0 — multi-model control plane

| Work package | Deliverable | Depends on | Initial capability |
|---|---|---|---|
| P0-A | Root repository consolidation and history preservation | none | complex implementation |
| P0-E | Historical-document classification and authority map | P0-A | mechanical execution plus standard review |
| P0-B | AGENTS.md and thin vendor adapters | P0-A, P0-E | standard implementation from approved design |
| P0-C | Schemas, policies, roles, routing, examples, and Level-1 agentctl | P0-A | complex implementation |
| P0-D | Product scope, invariant catalog, concurrency decisions, and ADRs | P0-A | work-package planning plus complex review |
| P0-F | Roadmap, program DAG, G0 criteria, and construction record | P0-B, P0-D | standard implementation |
| P0-I | Validators, cross-links, examples, dry run, and G0 integration | P0-B, P0-C, P0-D, P0-E, P0-F | complex integration |
| P0-G | Unassigned | none | no capability assigned |
| P0-H | Unassigned | none | no capability assigned |

`P0-G` and `P0-H` are intentionally unassigned because the canonical design names A–F and
I only. Their alphabetical absence is not permission to invent work.

The actual bootstrap ordering is authoritative evidence about concurrency: **P0-B strictly after P0-E**
because Task 3 moves the legacy `CLAUDE.md` and Task 4 creates the active
adapter. P0-D ∥ P0-C after P0-A. Within P0-C, schemas come first; policies and
roles/routing may then proceed concurrently before templates, examples, and agentctl consume
them. P0-I is the fan-in.

## P1 — structural foundation

P1 is deliberately serialized and contains no feature work:

```text
P1-A AppleBooks to CashBooks Xcode migration
→ P1-B platform and concurrency policy freeze
→ P1-C CashBooksCore package and app wiring
→ P1-D root build, test, and privacy commands
→ P1-E protected-test ownership and evaluation shell
→ G1
```

## P2 — contract and oracle freeze

| Task | Contract or oracle | Depends on |
|---|---|---|
| P2-A1 | Money, currency, and rounding | G1 |
| P2-A2 | Evidence IDs and lineage | G1 |
| P2-A3 | Injected clock and stable identifiers | G1 |
| P2-B | Synthetic corpus and fixture specification | G1 |
| P2-M0 | Installed Foundation Models SDK reconnaissance and feasibility ADR | G1 |
| P2-C | Document and draft DTOs | P2-A1, P2-A2 |
| P2-D | Fingerprint, trusted-contact, risk-signal, and decision DTOs | P2-A1, P2-A2 |
| P2-E | Document, extraction, validation, fingerprint, and risk protocols | P2-C, P2-D |
| P2-F | Confirmation command and atomic persistence contracts | P2-C, P2-D |
| P2-G | Headless operation and immutable snapshot contracts | P2-C, P2-D, P2-E, P2-F |
| P2-H | Protected invariant and metamorphic test packs | P2-C through P2-G |

P2-B and P2-M0 may run beside domain-contract work. P2-C ∥ P2-D after shared
primitives. P2-G follows the domain, risk, and persistence contracts; P2-H independently
owns the protected oracles. G2 freezes the interfaces and their hashes.

## P3 — parallel subsystem implementation

Five core implementation lanes fan out behind G2-frozen contracts:

```text
Document:       P3-D1 ∥ P3-D2 ∥ P3-D3 ∥ P3-D4 ∥ P3-D5 ∥ P3-D6 → P3-D7
Trust and risk: P3-T1 ∥ P3-T2 ∥ P3-T3 ∥ P3-T4 → P3-T5
Extraction:     P3-E1 → P3-E2 → P3-E3
Persistence:    P3-P1 → P3-P2 → P3-P3 → P3-P4 → P3-P5
Engine host:    P3-H1 → P3-H2 → P3-H3
```

Two supporting tracks join those lanes without changing their authority:

```text
Foundation Models: P2-M0 → P3-M3 → P3-M4 → P3-M5 → P3-M6 → P3-M7
Verification:      P3-Q1 → P3-Q2; P3-Q3; P3-Q4 → P3-Q5 → P3-I
```

P3-I consumes every P3 lane and performs headless iOS/macOS integration and failure
injection. G3-B closes the authorized baseline. G3-L remains a separate, additive live-model
claim.

## Contract materialization

P0's bootstrap artifacts are materialized now. P1 contracts are materialized at G0 against
the integrated G0 commit; P2 contracts at G1; P3 contracts at G2. This avoids contracts that
pretend to bind commits or interfaces that do not yet exist.
