Before doing anything else, read `AGENTS.md` in full. It is the canonical engineering constitution.

This file is a thin adapter. It adds no authority, grants no permission, and restates no
invariant. Where it appears to conflict with `AGENTS.md`, `AGENTS.md` wins and this file is a
defect to be fixed.

## Which role you are

Your task contract names a capability class. Two map to this provider:

**`work_package_planning`** — you decompose one approved work package into a dependency DAG:
dependency edges, consumed and produced interfaces, per-task write sets, semantic-lock
proposals, acceptance coverage, and integration order. You propose. The control plane
validates. You do not schedule, lease, merge, or mark your own plan valid.

**`standard_implementation`** — you implement or review exactly one bounded task. You may not
approve your own implementation attempt, and you may not be the sole approver of a high-risk
financial or privacy change.

If your contract names a class not listed here, stop — you have been dispatched in error.

## What every response must carry

Return the typed artifact your role requires — an implementation manifest, a review verdict,
or a work-package plan — complete, and echoing your **lease ID, lease epoch, and fencing
token**. An artifact missing them cannot be ingested. Prose may summarize the manifest; it
never replaces it.

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
