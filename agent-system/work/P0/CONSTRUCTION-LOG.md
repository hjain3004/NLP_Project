# P0 Bootstrap Construction Log

P0 was built by human-directed bootstrap work, not by the control plane it
creates. These rows are a construction record, not evidence of a
control-plane execution. The first machine-scheduled work in this repository
is the DRY-RUN work package; the first real forward contracts are P1's,
materialized at G0 exit against the integrated G0 commit.

This record is intentionally retrospective and factual. It does not invent leases, fencing
tokens, manifests, independent verdicts, or integration records for work that did not have
them. Commit trailers are the source for the first execution session. The Task 9 commit did
not record a session identifier; that gap is preserved as `not-recorded-in-commit` rather
than filled with a guess.

| Task | Title | Result commit(s) | Executing provider | Model | Model version | Session ID | Date | Verification final line |
|---:|---|---|---|---|---|---|---|---|
| 1 | Preflight verification and committed evidence | `73ba609137f1592050139b4576bee9b6142c392d` (evidence committed with Task 2) | Anthropic Claude Code | Claude Opus | 5 | `session_0183n79DKHi8SvUaC9VeKDRu` | 2026-09-01 | `preflight: ok` |
| 2 | Consolidate the repository under one Git root | `43ae7092fd4fe8c9610d71460848f0d0cad4bee0`, `6a203324f46e26edccf4da258e9210695bbef3aa`, `73ba609137f1592050139b4576bee9b6142c392d` | Anthropic Claude Code | Claude Opus | 5 | `session_0183n79DKHi8SvUaC9VeKDRu` | 2026-09-01 | `migration evidence: ok` |
| 3 | Establish document authority and classify historical material | `954158d20bb6688d8ebca845c69ac616a773fe7d` | Anthropic Claude Code | Claude Opus | 5 | `session_0183n79DKHi8SvUaC9VeKDRu` | 2026-09-01 | `authority classification: ok` |
| 4 | Author the constitution and thin vendor adapters | `10a94942bf2b0513b5c1bfc12d6c1b0183e8093f` | Anthropic Claude Code | Claude Opus | 5 | `session_0183n79DKHi8SvUaC9VeKDRu` | 2026-09-01 | `constitution and adapters: ok` |
| 5 | Author product, security, and decision documents | `8bfa00e3e7add0d43d4a1b771aa02ebb87bf69b3` | Anthropic Claude Code | Claude Opus | 5 | `session_0183n79DKHi8SvUaC9VeKDRu` | 2026-09-01 | `document set: ok` |
| 6 | Create the agent-system skeleton and artifact schemas | `bb2c508e194ae642c2c250d9dfc2bf364423c4c2` | Anthropic Claude Code | Claude Opus | 5 | `session_0183n79DKHi8SvUaC9VeKDRu` | 2026-09-01 | `schemas and skeleton: ok` |
| 7 | Encode policies | `f566013d7407579a3e99d355c0cfdb4fcace10c5` | Anthropic Claude Code | Claude Opus | 5 | `session_0183n79DKHi8SvUaC9VeKDRu` | 2026-09-01 | `policies: ok` |
| 8 | Encode role contracts and routing configuration | `31868b31829d8dd72be0e914fedb3092cc45c45d` | Anthropic Claude Code | Claude Opus | 5 | `session_0183n79DKHi8SvUaC9VeKDRu` | 2026-09-01 | `roles and routing: ok` |
| 9 | agentctl A — primitives | `3e49a7a082b263bf0682b766e49f47633e242ee2` | Anthropic Claude Code | Claude Sonnet | 5 | `not-recorded-in-commit` | 2026-09-01 | `agentctl A: ok` |
| 10 | Templates and must-fail fixtures | `623601c1937612a14f0cf247b316f42e909f598c` | OpenAI Codex desktop | GPT-5 family | runtime version unreported | `01a0d785-e25e-7122-a617-7d39c57875a3` | 2026-09-25 | `templates: ok` |
| 11 | Valid hash-linked fixture set | `ac1b4fe15ea1420cc5c4893893c39df5cea81eb4` | OpenAI Codex desktop | GPT-5 family | runtime version unreported | `01a0d785-e25e-7122-a617-7d39c57875a3` | 2026-09-25 | `valid examples: ok` |
| 12 | agentctl B — artifact validation | `4b7d2a492bb839f163db4184b61e0b296e41f693` | OpenAI Codex desktop | GPT-5 family | runtime version unreported | `01a0d785-e25e-7122-a617-7d39c57875a3` | 2026-09-25 | `make -C tools/agentctl test: OK` |
| 13 | agentctl C — event store | `db296dcfe6c80342d29a6adc132fbf530f6042dd` | OpenAI Codex desktop | GPT-5 family | runtime version unreported | `01a0d785-e25e-7122-a617-7d39c57875a3` | 2026-09-25 | `make -C tools/agentctl test: OK` |
| 14 | agentctl D — graph and locks | `9e2a163fe2d54e17334bebab9c3c71d6db3c681d` | OpenAI Codex desktop | GPT-5 family | runtime version unreported | `01a0d785-e25e-7122-a617-7d39c57875a3` | 2026-09-25 | `make -C tools/agentctl test: OK` |
| 15 | agentctl E — leases and scheduler | `b8ea9c167e5e170e906b4206ea7e03f03d422c39` | OpenAI Codex desktop | GPT-5 family | runtime version unreported | `01a0d785-e25e-7122-a617-7d39c57875a3` | 2026-09-26 | `make -C tools/agentctl test: OK` |
| 16 | agentctl F — context compilation and review independence | `9106a3593e29a5bebb8f055c7eb650413a006e59` | OpenAI Codex desktop | GPT-5 family | runtime version unreported | `01a0d785-e25e-7122-a617-7d39c57875a3` | 2026-09-26 | `make -C tools/agentctl test: OK` |
| 17 | agentctl G — approval, gate, and doctor | `be377b313c1b17948d41771e697298618bbf65c6` | OpenAI Codex desktop | GPT-5 family | runtime version unreported | `01a0d785-e25e-7122-a617-7d39c57875a3` | 2026-09-26 | `make -C tools/agentctl test: OK` |

## Verification note

The Task 18 plan names `agentctl plan validate` and `agentctl docs generate gates`, but those
commands were not present at the Task 18 base commit and reusable documentation tooling is
owned by Task 19. Task 18 therefore uses the bounded schema validator plus the graph library
for equivalent work-package and acyclicity checks, and renders `PHASE_GATES.md`
deterministically from `G0-criteria.json`. Task 19 must implement the reusable generator and
prove byte equality; this note is evidence of the sequencing defect, not a waiver.
