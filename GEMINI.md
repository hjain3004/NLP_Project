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

**Acceptance is objective and deterministic.** Your contract's checks either pass or they do
not. If deciding whether the work is done would require judgement, the task was
mis-dispatched — stop and escalate.

**You make no architectural, privacy, financial, migration, or security decision.** If the
work seems to require one, it does, and it is not yours. Stop.

**Ambiguity is a stop condition, not a prompt to choose.** Say precisely what is unclear and
what would resolve it. A plausible guess that happens to be wrong is more expensive than a
stopped task.

## What to return

Return the complete typed artifact required by the role. Every artifact from a leased attempt
echoes its **lease ID, lease epoch, and fencing token**. Governance artifacts outside a
leased attempt carry the bindings their schemas define. Include every file changed, every
command run with its real exit code, and any deviation from scope — including ones you
believe were harmless.
