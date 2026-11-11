# CashBooks Concurrency Decisions

**Status:** Active engine architecture.
**Written:** P0 Task 5, 2026-09-01.

This records an architectural decision, not a signature. The binding Swift signature is frozen at Milestone 1B and may differ in spelling; a divergence in spelling is not a defect, a divergence in meaning is.

These decisions constrain the package structure and actor boundaries in the engine build
milestones.

## Ownership

**`PDFDocument` and `PDFPage` are owned by a dedicated document actor.** They are
non-`Sendable` framework objects. Page text extraction and rasterization happen inside that
owner boundary; everything downstream receives value snapshots. (PLAT-02)

**Vision work is actor- or request-owned**, with bounded page concurrency from a configurable
policy. Results carry their page index and are **sorted before document assembly**, so page
order is deterministic regardless of completion order. Unbounded OCR concurrency is the
easiest way to exhaust memory on a phone with a large scan.

**Foundation Models sessions are per request, or owned by a dedicated adapter actor.**
Sessions are not shared across simultaneous imports. Tool use is disabled for
untrusted-document extraction.

**SwiftData uses a `ModelActor`-backed repository.** Model instances and `ModelContext` never
leave it. Callers receive stable IDs and immutable Core snapshots. (PLAT-04)

```text
Sendable engine commands
    ↓
actor-owned local-engine coordinator
    ↓ immutable commands and snapshots
PersistenceRepository protocol
    ↓
ModelActor SwiftData repository
    ↓
model records and ModelContext        ← never cross this line
```

## The boundary the future client sees

The headless coordinator publishes **immutable `Sendable` snapshots**. It does not know how
anything is rendered and does not import SwiftUI. (PLAT-03)

A future client may submit explicit engine commands, observe snapshots, supply corrections
with evidence provenance, and request confirmation authorization through a trusted host
boundary. It may not call PDFKit, Vision, Foundation Models, or SwiftData directly.

Isolation choices *inside* a future client are a decision for the UI program. They are not a
condition of the headless engine milestones, and nothing here constrains them.

## Cancellation

Cancellation is **cooperative and checked between expensive operations** — before and after
each render, each recognition pass, and each model request. A cancelled import leaves no
partial canonical record. (PLAT-07)

Each import carries a **generation ID**. Late results from a superseded import are rejected
on arrival rather than merged. Import B starting must cancel import A *and* refuse A's
completion if it arrives afterwards; the first without the second is a race that silently
writes stale data.

## Core DTOs

Every value crossing an actor boundary is immutable and `Sendable`. Framework objects and
SwiftData model instances never do. `CashBooksCore` itself compiles without Foundation
Models, PDFKit, Vision, SwiftUI, SwiftData, or networking, which is enforced by a
forbidden-import check rather than by convention. (PLAT-01)

## What structural work must not do

Structural work sets the platform matrix and the project's actor defaults. It may not:

- adopt a default isolation that forces framework objects across actors;
- introduce a shared mutable document cache;
- make `CashBooksCore` depend on any framework listed in PLAT-01.

## Related

- `docs/security/INVARIANTS.md` — PLAT-01 through PLAT-07
- `docs/product/ACTIVE_SCOPE.md` — the platform decision these constrain
