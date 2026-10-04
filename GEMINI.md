Before doing anything else, read `AGENTS.md` in full. It is the canonical engineering constitution.

This file is a thin adapter. It adds no authority and restates no invariant. Where it appears
to conflict with `AGENTS.md`, `AGENTS.md` wins.

## Your role

Your task contract and role contract name the capability class or operational role for this
dispatch. This adapter does not bind a provider to any class. Capability-class bindings are
read only from `agent-system/routing/capability-registry.json` by the control plane.

## Rules

**Do exactly what the contract says, and nothing adjacent.** Not the neighbouring file, not
the obvious small improvement, not the typo you noticed. Report those; do not fix them.

Use only the authority, decision rights, checks, and escalation triggers supplied by the
injected role contract and task contract. This adapter neither narrows nor broadens them.

## What to return

Return the complete typed artifact required by the role. Every artifact from a leased attempt
echoes its **lease ID, lease epoch, and fencing token**. Governance artifacts outside a
leased attempt carry the bindings their schemas define. Include every file changed, every
command run with its real exit code, and any deviation from scope — including ones you
believe were harmless.
