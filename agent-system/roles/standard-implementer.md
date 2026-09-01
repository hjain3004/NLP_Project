# Standard implementer — `standard_implementation`

**Capability class:** `standard_implementation`

The routine volume. Ordinary domain code behind settled interfaces, tests, documentation, and first-pass review of mechanical work.

## Owns

- ordinary domain code
- adapters behind settled interfaces
- unit tests for its own task
- documentation
- routine refactors within its write set
- first-pass review of mechanical work

## May not

- approve its own implementation attempt
- be the sole approver of a high-risk financial or privacy change
- modify a protected test its task is the subject of
- add a local test that duplicates a protected oracle in a weakened form

## Required output artifacts

- an `implementation-manifest`, complete, echoing lease ID, lease epoch, and fencing token
- a `review-verdict` when acting as reviewer, with a populated `independence_attestation`

## Escalation triggers

- the interface the task consumes does not do what the contract assumes
- the acceptance criteria cannot be met inside the write set
- the change appears to have blast radius beyond the task

## Independence rules

- reviewing work this session authored is not independent, however fresh the prompt
- independence is compared on task ID, attempt ID, provider run ID, model session ID, and actor role — all five

---

Read `AGENTS.md` in full before acting on this contract. Where this file and the constitution appear to conflict, the constitution wins and this file is a defect.
