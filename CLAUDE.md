Before doing anything else, read `AGENTS.md` in full. It is the canonical engineering constitution.

This file is a thin adapter. It adds no authority, grants no permission, and restates no
invariant. Where it appears to conflict with `AGENTS.md`, `AGENTS.md` wins and this file is a
defect to be fixed.

## Which role you are

Your task contract and role contract name the capability class or operational role for this
dispatch. This adapter does not bind a provider to any class. Capability-class bindings are
read only from `agent-system/routing/capability-registry.json` by the control plane.

## What every response must carry

Return the typed artifact your role requires — an implementation manifest, a review verdict,
or a work-package plan — complete. Every artifact from a leased attempt echoes its **lease
ID, lease epoch, and fencing token**. Governance artifacts outside a leased attempt carry
the bindings their schemas define. Prose may summarize a typed artifact; it never replaces
it.

## Stopping

Stop at the task boundary. Do not start the next task, tidy an adjacent file, or fix
something you noticed outside your write set. Report it as a discovered dependency or a
finding and let the control plane decide.

Ambiguity is an escalation, not a choice to make quietly. Say what is ambiguous, say what you
would need to resolve it, and stop.

## What is not state

Chat is not state. Your prior turns in this session are not state. What you remember about
this repository from another session is not state. The repository, the contracts, the
evidence, and the event store are the record — read them rather than recalling them.
