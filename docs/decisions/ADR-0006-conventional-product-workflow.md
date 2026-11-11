# ADR-0006 — Conventional product workflow supersedes the control-plane program

## Status
status: accepted (by owner merge of the Milestone 0 pull request)

## Context
From 2026-09-01 to 2026-11-10 the repository built a multi-model control plane (P0) whose
gate G0 was never closed. No product code was written. An inspection at commit 6020818
found that G0-12 could not be satisfied as specified (audit context ceiling far below the
audited material; auditor independence resting on controller-asserted identities; any
repair invalidating the audit), and that the candidate-check sandbox is Linux-only while
the product requires macOS and Xcode.

## Decision
Phases P0, P1, P2 and gates G0, G1, G2 are withdrawn as prerequisites. The headless engine
is built with ordinary pull requests, CI, and cross-model review, as described in
docs/ENGINE_BUILD_PLAN.md. ADR-0001 through ADR-0005 are superseded. Product scope,
non-goals, the 26 invariants, and the G3-B and G3-L claim definitions are unchanged.

## Consequences
The control plane is preserved at tag archive/control-plane-2026-11 and removed from the
working tree. Typed approval records, leases, routing, and phase gates no longer exist;
owner authority is exercised by merging pull requests under branch protection. Changing an
invariant statement still requires the owner's explicit decision in a dedicated PR.
