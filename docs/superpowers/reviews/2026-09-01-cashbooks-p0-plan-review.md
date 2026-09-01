# CashBooks P0 Control-Plane Bootstrap Plan — Independent Review

**Subject:** `docs/superpowers/plans/2026-09-01-cashbooks-p0-control-plane-bootstrap.md`, revision 1
**Date:** 2026-09-01
**Reviewers:** four fresh-context independent reviewers, plus empirical verification of every Git claim
**Outcome:** revision 2, incorporating all Critical and Important corrections
**Verdict:** **Revision 1 was not executable. Revision 2 is ready for execution.**

---

## 1. Review method

The plan's author was not its reviewer. Four reviewers ran in parallel, each in a fresh session with no access to the authoring conversation, each given one dimension and told explicitly not to report on the others:

| Reviewer | Dimension |
|---|---|
| R1 | Execution safety, step ordering, and the Git migration |
| R2 | Verification integrity — does each check prove what its step claims? |
| R3 | Specification conformance, scope discipline, authority holes |
| R4 | Feasibility, task sizing, dependency realism |

Every finding was then verified against the plan text before acceptance. Findings that could be settled by running a command rather than by reasoning were run: a fixture reproducing the CashBooks layout (a nested repository whose tree root holds `AppleBooks/`, `AppleBooks.xcodeproj/`, and `AppleBooksTests/`) was built on git 2.43 and the migration was executed end to end.

This matters because three of revision 1's Git assertions were wrong in ways that reasoning alone would not have settled, and one assertion a reviewer suspected was wrong turned out to be correct.

## 2. Empirical verification of Git claims

```text
CLAIM                                   RESULT
git bundle verify <path>                REFUTED. "error: need a repository to
  from the repository root              verify a bundle" — the root is not yet a
                                        repository at that point in the plan.

git merge-base --is-ancestor            CONFIRMED CORRECT (exit 0). `-s ours`
  <NESTED_HEAD> HEAD                    discards the tree, never the parent link.
                                        R1 flagged this for scrutiny and it survived.
                                        Left unchanged in revision 2.

git diff --stat <NESTED_HEAD> HEAD      REFUTED. Produces a three-file diff on a
  -- AppleBooks/                        CORRECT migration, because the imported
  as the "no application code           commit's tree root is the app while HEAD
  changed" proof                        nests it one level down.

git rev-parse HEAD:AppleBooks    ==     CONFIRMED. Both 060b6a13… on a correct
git rev-parse <NESTED_HEAD>^{tree}      migration. This is the right proof.

MERGE_HEAD left by an interrupted       CONFIRMED. A later commit messaged
merge --no-commit is consumed by        "docs: establish document authority"
the NEXT commit from any task           acquired two parents and silently became
                                        the history-import merge.
```

The last one is the most dangerous defect found in the review, because revision 1's own reachability check (`--is-ancestor`) would still have returned 0 afterward — certifying a migration in which `AppleBooks/` was absent from the tree.

## 3. Findings summary

| Severity | Count | Status |
|---|---|---|
| Critical | 12 | all corrected in revision 2 |
| Important | 24 | all corrected in revision 2 |
| Minor | 9 | 8 corrected, 1 accepted as residual |

## 4. Critical findings

### C-01 — `git bundle verify` aborts the migration at its second command
**Reviewer:** R1. **Verified empirically.**
Task 2 Step 1 ran `git bundle verify` from the repository root, which Task 1 Step 1 had just asserted is *not* a repository. Under the plan's own "if the expected output does not appear, stop" rule, the operator halts fourteen tasks deep at command two — or, worse, reads the non-zero exit as a bad bundle and re-creates or abandons the backup.
**Corrected:** `git -C AppleBooks bundle verify`. Expected output also corrected to name **two** refs (`refs/heads/main` and `HEAD`), which `--all` emits.

### C-02 — the backup path could not survive a step boundary, and its guards passed on a mislocated move
**Reviewers:** R1, R4.
`CB_BACKUP="$(mktemp -d)"` was set once in Step 1 and dereferenced through Step 7, but each step is a separate shell invocation. With the variable empty, `mv AppleBooks "$CB_BACKUP/AppleBooks-working-tree"` becomes `mv AppleBooks /AppleBooks-working-tree` — and **both** of revision 1's guards still printed `ok`, because `test ! -e AppleBooks` checks only that the source is gone and `test -d "$CB_BACKUP/…/.git"` with an empty variable checks the very path the tree just landed in. The plan's mitigation was a prose note telling the operator to "record it in the task notes", which an agent has no reason to execute. Compounding it: `mktemp -d` lands under `$TMPDIR` on macOS, subject to periodic cleanup, and between Steps 2 and 6 it holds **the only copy of the application source** — so the plan's claim "nothing is removed by this plan" held only while `$TMPDIR` survived.
**Corrected:** a fixed path under `$HOME`, re-declared at the top of every step that uses it, with a `test -d` precondition, and a destination-anchored guard that compares `git rev-parse HEAD` in the moved tree against the recorded `NESTED_HEAD`.

### C-03 — no `MERGE_HEAD` guard and no abort path
**Reviewer:** R1. **Verified empirically.**
See §2. Any interruption between `git merge --no-commit` and `git commit` leaves `MERGE_HEAD`, and the next commit by any later task consumes it.
**Corrected:** `test ! -f .git/MERGE_HEAD` precondition in Task 2 Step 6 and at the head of Task 3; an explicit recovery paragraph naming `git merge --abort`; and a post-commit `test "$(git rev-parse HEAD^2)" = "$NESTED_HEAD"` so partial completion fails loudly.

### C-04 — the "no application code changed" proof failed on correct work
**Reviewer:** R1. **Verified empirically.**
See §2. The plan's most load-bearing safety claim would never have passed.
**Corrected:** tree-hash equality (`git rev-parse HEAD:AppleBooks` vs `<NESTED_HEAD>^{tree}`) in Task 2 Step 8 and in the final verification. The `diff -qr` is retained as a secondary human-readable check with ignored classes excluded and made explicitly non-fatal.

### C-05 — no `set -e`: ten of fourteen pass-lines were unconditional echoes
**Reviewers:** R1, R2.
No verification block contained `set -e` or a shebang. Blocks mixed guarded checks (`… || { echo FAIL; exit 1; }`) with bare `test`/`rg -q`, and the bare ones are inert without `set -e`. Verified: `bash -c 'test -f /nonexistent; grep -q zzz /etc/hostname; echo "document set: ok"'` prints `document set: ok` and exits 0. Since every Expected block in the plan instructs the agent to look for the `: ok` line, a task with every check failing reports success. This affected the evidence for G0-04, G0-10, and G0-11.
**Corrected:** every one of revision 2's 57 bash blocks opens with `set -euo pipefail`, verified mechanically. R1 correctly noted this must not be done before fixing I-03, I-04, I-05 and C-04, which would otherwise become hard aborts on correct artifacts — all four were fixed in the same pass.

### C-06 — gate closure was a self-assertion
**Reviewer:** R3.
The worker hand-wrote `G0-evidence.json` **including each criterion's `result`**, and `gate.py` was specified only to "evaluate a phase gate against its criteria" — nowhere to re-run the underlying checks. The closing assertion then read back the worker's own claim. Fourteen `"result": "pass"` strings closed G0. Nine of the criteria reduced to prose references like `Task 13 Step 2`, which are not machine-resolvable at all.
**Corrected:** `phase-gate.schema.json` gains `evidence_command`, `expected_exit_code`, and `evidence_hash`. `gate.py` **discards and recomputes** any `result` in its input, re-runs each command from the trusted base, and writes results itself. A criterion whose evidence cannot be re-executed may only be `open` or a bound attestation — never `pass` on a claim.

### C-07 — the approval bound a file that the gate step then rewrote
**Reviewers:** R1, R3.
The approval hashed `G0-evidence.json` while `gate_result` was `open`; the next step wrote `gate_result: passed` into that same file. By the plan's own stated rule — "if the evidence file changes afterward, the approval is automatically invalid" — the approval died at the instant the gate closed, and reissuing it would change the file again. No exit. The approval was also bound to the **wrong target**: G0-13 and G0-14 approve *the active product scope* and *the authority map*, but the hash covered a gate record, so `ACTIVE_SCOPE.md` could be edited the next day with the approval still valid.
**Corrected:** three artifacts. `G0-criteria.json` is immutable and carries no results — the phase approval binds it. `G0-scope-statement.json` carries the SHA-256 of `ACTIVE_SCOPE.md`, `NON_GOALS.md`, `INVARIANTS.md`, and `DOCUMENT_AUTHORITY.md` — the scope approval binds that. `G0-result.json` is where `agentctl gate` writes. `agentctl gate` never writes into a file an approval binds.

### C-08 — human approval was forgeable, and the stated toolchain cannot sign
**Reviewers:** R3, R4.
Three independent holes. **No task provisioned a signing key** — so whoever first ran `agentctl approve` would generate one, and the agent executing the plan is the one running commands; a seal whose subject can mint the key is not a seal. **`isatty()` is not identity**, and `phase_authorization` sat in the weak tier whose only floor was a TTY, which an agent running through a pty satisfies trivially — and that is the exact approval G0 depends on. And **Python's standard library has no asymmetric signing primitive** — no Ed25519, no RSA — so under the plan's own standard-library-first constraint, `control_plane_signature` could only ever have been an HMAC, which an implementer would have had to invent silently.
**Corrected:** a human-run key-provisioning step (Task 17 Step 1) writing a 32-byte key at mode 0600 outside the repository, with its fingerprint recorded in a committed `approval-principals.json`; `doctor` fails on an absent fingerprint, a key inside a worktree, a mode wider than 0600, or an mtime post-dating the first `agentctl` commit. The field is renamed `control_plane_seal`, and **ADR-0005** records honestly that it is an HMAC-SHA256 keyed MAC — it prevents accidental and in-worktree forgery, not an attacker with local key read access. The tier `interactive_tty_with_key_unlock` additionally requires the key-unlock passphrase.

### C-09 — `agentctl` was a ~12,000-line monolith with all-or-nothing acceptance
**Reviewer:** R4.
Twenty-two modules and thirteen test files: canonical hashing, a JSON Schema subset validator, a SQLite event store with WAL and single-writer locking, artifact validation, a DAG with descendant invalidation, glob-aware path and semantic locks, leases with epochs and fencing tokens, a scheduler with a twenty-row state machine, context compilation, review independence, an integration queue, gate evaluation, approvals, and a twenty-command CLI. Sized at 6,000–8,500 production LOC plus 2,500–4,000 test LOC, or 15–24 agent sessions — **40–50% of the plan's entire budget in one unit that cannot be partially delivered.** Acceptance was one `unittest discover` plus a fixture loop; one unimplemented corner blocked the commit for all of it. It was also written blind, discovering whether it worked only in the dry run, at which point a defect reopened the whole thing with no repair protocol. This is precisely the budget-exhaustion failure the owner has already lived through once on another assistant.
**Corrected:** seven sequenced tasks (9, 12–17), each with its own executable proof, plus `cli.py` restructured as a thin dispatcher over `commands/<name>.py` so no two tasks edit the same file.

### C-10 — canonical hashing was required before it was defined
**Reviewer:** R4.
Task 10 required "a real computed SHA-256 of the referenced content, not a placeholder", while the canonicalization rule (sorted keys, no whitespace variance, `ensure_ascii=False`) was defined in Task 11, afterward. A one-byte difference would surface as a test failure inside the plan's most expensive task with its root cause sitting in committed fixtures. The plan also never said whether `contract_hash` covers the contract with that field excluded, zeroed, or present — a self-referential hash is undefinable by guess, and it was load-bearing in five tasks.
**Corrected:** the canonicalization rule and the exclude-the-field rule are stated normatively in Task 6; `hashing.py` moves into Task 9, before the fixtures; templates and must-fail fixtures (Task 10, hash-free, mechanical) are separated from the hash-linked valid examples (Task 11), whose acceptance recomputes every hash field.

### C-11 — the retrospective P0 task contracts were mechanically impossible
**Reviewers:** R3, R4.
Task 12 required "one task contract per plan task … each binding to the actual base commit produced by the preceding task", executed before Tasks 13 and 14. Those contracts must bind commits that do not exist when the file is written; a git hash cannot contain itself. `expires_at` was required with no honest value for a retrospective contract; the ID range spanned eight work packages for thirteen tasks; and the check `assert len(tasks) >= 9` would catch none of it. **No G0 criterion referenced them.** Beyond impossibility, they were actively misleading: the repository would hold validated, hash-bound contracts for fourteen tasks that ran with no lease, no fencing token, no manifest, and no verdict — which the P1 planner and the architecture auditor would reasonably read as evidence P0 ran under control-plane discipline.
**Corrected:** deleted. `CONSTRUCTION-LOG.md` records what actually happened, with a header stating plainly that P0 was human-directed bootstrap work and not a control-plane execution. The log's session IDs are what make the audit's independence check possible (see C-12 and I-22). `task-contract.schema.json` gains a required `execution_mode` field so the distinction is machine-visible if such a contract is ever written.

### C-12 — the dry run could not execute in its stated order, and three of its inputs did not exist
**Reviewers:** R1, R4.
Four compounding defects in the task that produces five G0 criteria. **DRY-001's write target `agent-system/examples/README.md` is created by no task** — it appears exactly once in the plan, at its point of use. **Both failure fixtures** (`DRY-001-late-manifest.json`, `DRY-002-self-review.json`) were consumed but never authored, and since `agentctl ingest` on a nonexistent file exits non-zero — the exact outcome those steps checked for — **they would have passed for the wrong reason and printed their success lines with nothing tested.** Step 5 attempted a self-review ingest against a task never dispatched, so it would fail with "no attempt to review" rather than on identity. Step 6 bumped `contract:core-domain-v1` and asserted a task consuming nothing became stale. Step 7 tested altered-patch invalidation on a candidate with no ingested attempt and no verdict, and Step 8 — which establishes those preconditions — ran afterward.
**Corrected:** `examples/README.md` created in Task 6; both fixtures authored in Task 19 Step 1 with `test -f` guards before use; DRY-002's contract declares `consumes: ["contract:core-domain-v1@1"]`; the sequence is reordered so DRY-001 reaches `independent_review` before self-review is attempted; the altered-patch test moves to P1 with the integration queue (see I-18).

## 5. Important findings

| ID | Finding | Correction |
|---|---|---|
| I-01 | The `AGENTS.md` provider-neutrality grep contradicted its own §5. §5 required summarizing seven roles; four role files were vendor-named (`gemini.md` among them); the grep was `rg -qi 'claude\|gemini\|codex\|gpt-5'`. A faithful implementation failed its own verification **after** writing the file. | Role files renamed to capability classes (`architect.md`, `planner.md`, …); `AGENTS.md` refers to roles by class; the grep narrowed to vendor product names with word boundaries. |
| I-02 | The `DOCUMENT_AUTHORITY.md` check grepped the file for its own filename, which nothing in the authoring step required — so it failed on a correct document. | Replaced with three checks against content the step actually mandates. |
| I-03 | The banner diff was asserted as three inserted lines; a three-line banner plus a blank separator is four. An agent obeying "stop if expected output does not appear" halts on correct work. | `git diff --numstat` asserted as exactly `4 0` per file. |
| I-04 | `agentctl debug` was guarded to refuse "when `agent-system/work` contains a non-DRY-RUN work package in an active state" — which Task 12 then creates, making Task 13's failure-path steps impossible and stranding G0-06 and G0-09. | Guard narrowed to exempt `bootstrap_phase: true` work packages before G0. |
| I-05 | All dry-run evidence lived under the git-ignored `agent-system/runtime/`; the synthetic edits were reverted; what survived into the clean checkout was a worker-typed `RESULTS.md` transcript that G0-05 through G0-09 referenced. The single largest self-authorization surface after C-06. | `agentctl events export` writes a committed `evidence/events.json`; specific `reason_code`s are asserted in it; Task 20 replays it from a fresh clone. |
| I-06 | G0-06 and G0-07 were proven by **one** fixture pair contending on both a path and a semantic lock — so a single rejection was consistent with either mechanism being entirely unimplemented. | Two distinct pairs: DRY-005/006 (glob containment, no semantic lock → `path_lock_conflict`) and DRY-007/008 (different files, shared lock → `semantic_lock_conflict`), each asserting its reason code. |
| I-07 | G0-11 requires documentation checks from a clean checkout; the plan built none. Its document assertions were one-shot inline shell, never re-runnable, and the "documentation and control-plane check" contained only `doctor`, `unittest`, and a validation loop. | `agentctl docs verify` re-runs every document assertion plus a link check, a byte comparison of the authority block against its JSON source, and a claims lint. |
| I-08 | The authority order omitted the machine policies the scheduler actually enforces, the canonical specification, and the plan currently executing — in the one document whose job is to have no gaps. It also made a human-approved level-1 document simultaneously outranked by an unranked one. | Nine levels including `1b. Machine-enforced policy`, `2b. The canonical engineering design`, and `4b. The active phase plan`, plus an explicit rule that a level-1/specification conflict escalates and **neither** executes. |
| I-09 | `agentctl validate <path>` must choose among ten schemas; the plan never said how, and `additionalProperties: false` everywhere blocked a discriminator field. Called in three tasks and at the gate. | Every schema declares required `artifact_type` with a `const` equal to its stem; an unknown or absent type exits with `SchemaUndeterminable`. |
| I-10 | Fixtures `07-stale-fencing` and `09-event-contains-document-text` were looped through a **stateless** validator. Staleness exists only relative to store state; "document text" was never defined detectably. | Fixtures split into `invalid/static/` (6) and `invalid/stateful/` (3), each stateful one owned by the subsystem that can seed the store. The event rule is now objective: a denylisted key or a string over 2048 bytes. |
| I-11 | Task 13 drove `agentctl ready --json`, `integrate --simulate-patch-alteration`, and asserted on `state`, `orphan_evidence_count`, and `events` — none specified. Task state names were never enumerated anywhere. | A normative CLI surface, `--json` output shapes, and the full state enumeration are fixed before the implementation tasks; the transition table is a module-level structure whose row count is asserted. |
| I-12 | `PYTHONPATH=tools/agentctl` is relative; Task 11 Step 10 ran `cd tools/agentctl` first, resolving it to a nonexistent path. Two different test invocations existed, and the first could not work. | Absolute `PYTHONPATH` from `git rev-parse --show-toplevel`; `make -C tools/agentctl test` is the single invocation used everywhere. |
| I-13 | Tasks 4 and 6 required invariant IDs "matching `invariants.json`" — created by Task 8, afterward. The ID set was discoverable only by reading that later task, and nothing cross-checked the three homes. The exact drift the system exists to prevent was unguarded across its three most authoritative documents. | The 26-ID table is normative in the plan's Global Constraints; a three-way set-equality check runs in Task 7 and again in the final verification. |
| I-14 | "P0-B through P0-E may execute concurrently" is false: Task 3 runs `git mv CLAUDE.md` and Task 4 creates `CLAUDE.md`. Broad `git add -A` in three tasks would have swept in concurrent tasks' in-flight files. | The true concurrency is documented; every task stages its exact paths; serial execution is the stated recommendation given the budget. |
| I-15 | `verify.py`, `integrate.py`, `routing.py`, the integration test suite, and two dry-run steps map to **no G0 criterion** — ~1,500–2,000 LOC and 3–5 sessions of the least useful code at P0, since nothing exists to integrate until P1. | Deferred to P1 entry as declared stubs raising `NotImplementedAtLevel1`, with tests asserting the raise. The CLI surface and adapter protocol stay fixed. |
| I-16 | Six architecture and threat documents were required at P0 though no G0 criterion names them, and `PERSISTENCE.md` would have frozen a DTO field list that G2's oracle review is supposed to own — pinning two semantic locks against P0 prose rather than a real interface hash. | Moved to P1 and P2. `CONCURRENCY.md` stays, carrying a verbatim line stating it records a decision, not a signature. |
| I-17 | Task 11's acceptance validated **one** of seven valid examples and discarded its exit code, then required all must-fail fixtures to be rejected. `def validate(path): sys.exit(1)` passed. | A mandatory accept-loop over all valid examples runs **first**; each must-fail test asserts a distinct error code matching its filename. |
| I-18 | `unittest discover` on an empty or misnamed `tests/` prints `Ran 0 tests … OK` and exits 0, and the exit code was discarded. A `tests/` directory containing one helper satisfied the deliverable. | Test acceptance is per-subsystem and assertion-specific across seven tasks; `make test` propagates exit status. |
| I-19 | Task 13 Step 9's prose asked for a "revert" beside a code block containing two read-only commands, while the DRY-RUN tree was still untracked. `git clean -fdx` — a natural agent choice — would have deleted all of it plus the runtime evidence. | An explicit narrow `git checkout --` on the six edited files, with `git clean` / `reset --hard` / `checkout -- .` prohibited in the step text, plus post-conditions asserting the evidence survives. |
| I-20 | The architecture audit (G0-12) was an unschema'd Markdown file with no ingest path, no identity binding, and an independence constraint that was unverifiable in principle because the plan recorded who authored nothing. | A tenth schema (`architecture-audit.schema.json`) with auditor session/run identity and decision coverage; `agentctl audit ingest` rejects an audit whose session appears in `CONSTRUCTION-LOG.md`; the audit is split into five fresh-context passes plus a merge, since 73 rows against the whole corpus does not fit one context window. |
| I-21 | The path-lock rule was never defined, and its only test had two tasks writing the **identical literal path** — which plain string equality passes, closing G0 while failing to detect the first real overlapping-glob conflict in P1. | A normative containment rule (`a/**` contains `a/b/c`; `a/*.json` conflicts with `a/x.json`; `a/*.json` and `a/x.md` do not), with three separately-asserted cases and a glob-containment dry-run pair. |
| I-22 | No valid `human-approval` fixture existed; only rejection paths were tested. The accept path for the one artifact G0 depends on was first exercised at the gate, by the human, with no fixture to diagnose against — and the failure most likely to go unnoticed is one that accepts too readily. | An eighth valid example plus a positive acceptance test, and a one-byte `target_hash` mutation that must be rejected. |
| I-23 | Gate criteria existed in three places — `PHASE_GATES.md` (declared the source `agentctl gate` reads, requiring unspecified Markdown parsing), `program-dag.json`, and a hand-assembled evidence file — with no precedence and no drift check, and the "source" was modified after evaluation. | `G0-criteria.json` is the single machine source; `PHASE_GATES.md` is generated by `agentctl docs generate gates` and byte-compared by `docs verify`; only G0's criteria are transcribed now, the other 49 at their preceding gates. |
| I-24 | The task-contract ID range implied `P0-G` and `P0-H`, which the design does not define. A worker would either drop two deliverables or fabricate two work packages into the file `agentctl` treats as authoritative. | Recorded explicitly as `status: "unassigned"` with the reason, and asserted. |

## 6. Minor findings

| ID | Finding | Correction |
|---|---|---|
| M-01 | No git version floor, though `git init -b` requires 2.28 — failing **after** `AppleBooks/` had been moved out of the tree. | Asserted in Task 1 Step 1 alongside the Python floor. |
| M-02 | `git add … docs` would have committed five large research Markdown extracts the file map called untracked, placing them inside the tree the context compiler reads. | Added to `.gitignore`; classified level 7 in `docs/archive/README.md`. |
| M-03 | The final UI-leak check grepped every task contract for `SwiftUI\|design token`, but `non_goals[]` is a required field — an honest contract tripped it. | Field-scoped to `writable_paths` and `acceptance_behavior`. |
| M-04 | `! rg -q '"phase_id": "P4"'` guarded against a phase ID the plan never assigns anywhere; the assertion could never fire. | Replaced with a positive assertion that phases are exactly `{P0,P1,P2,P3}`. |
| M-05 | Two different provider-neutrality greps, the final one weaker than the task-level one (dropping `Gemini` and `Sol`). | One form, applied to schemas, policies, roles, and routing policy. |
| M-06 | `agent-system/runtime/` is git-ignored and empty, so it does not survive a clone; Task 13's redirect and Task 14's clean-checkout run both assumed it. | `mkdir -p` at both points; `config.py` creates it on first use. |
| M-07 | The Makefile was promised in Global Constraints but no step authored it. | Authored in Task 9 Step 1 with `env`, `test`, `check` targets. |
| M-08 | `EX-DRY-001` (Task 10) was said to be reused by the dry run, which never referenced it. | Renamed `EX-DOC-001` and stated as a standalone fixture set. |
| M-09 | `diff -qr -x .git -x xcuserdata` fails on any git-ignored-but-present file (`.DS_Store` is near-certain in an Xcode project browsed in Finder), so the integrity assertion reads as failure on a correct migration. | **Accepted as residual.** The exclusion list was widened and the check made explicitly non-fatal, but it remains a human-judgement check. The authoritative proof is now tree-hash equality, which has no such ambiguity. |

## 7. Findings raised and rejected

- **R1 flagged Task 2 Step 8's `git merge-base --is-ancestor` for scrutiny**, on the theory that `-s ours` might break ancestry. Tested: it returns 0. `-s ours` discards the *tree*, never the parent link. The assertion is correct and was left unchanged.
- **Blanket-adding `set -e` was proposed as a standalone fix.** Rejected as a standalone change: with I-01, I-02, I-03 and C-04 unfixed, the first `set -e` run would hard-abort on correct artifacts. The four were corrected in the same pass.

## 8. What revision 1 got right

Not everything needed changing, and the parts that held are the parts that carry the design's intent:

- **P1/P2/P3 contract deferral** was correctly honored and machine-enforced, including the assertion that every non-P0 phase carries `contract_materialization: deferred_to_preceding_gate`.
- **No UI work leaked** into the active program: `NON_GOALS.md`, the constitution's §1 prohibition, the UI-free hero scenario, and an explicit "what this plan deliberately does not do" section.
- **Anti-fabrication defaults were right throughout:** `baseline: null` on every metric with the stated rationale, `challenger_merge_eligible: false`, `binding_type: "initial_prior"`, `model_version: "unpinned"`, an empty protected-test registry at P0, `"retry_without_new_evidence": "forbidden"`, and an `open` gate result so G3-L can stay honestly unproven without failing its phase.
- **Schema-level separation of duty was well designed:** `secret_access` as `const: false`, `maximum_attempts` capped at 3, `candidate_patch_unchanged` as `const: true` with the reasoning that a manifest which cannot honestly set it cannot be written, and an `independence_attestation` block that makes independence comparable rather than claimed. The nine must-fail fixtures were the right instinct — the gap was that the *gate-level* artifacts received none of that rigor.

## 9. Cost effect of the revision

R4's session estimate for revision 1 was **38–52 agent sessions**, with `agentctl` alone at 40–50% of the total. Revision 2's deferrals — the integration and verification subsystem, six architecture documents, the retrospective contracts, and 49 gate criteria — remove **roughly 7–9 sessions, about 20% of the plan, with G0's evidentiary strength unchanged.** The `agentctl` split does not reduce total work, but it converts one indivisible 15–24-session unit into seven independently recoverable ones, which is the difference between a defect costing a sub-task and a defect costing the build.

## 10. Open items carried into execution

1. Verify the exact installed Foundation Models APIs — **P2-M0**.
2. Measure safe Xcode and Core build concurrency on the M4 Pro — **P1-D**, feeding `concurrency.json`.
3. Confirm provider sandbox and structured-output capabilities before each adapter — **automation Level 3**.
4. Select a pinned JSON-schema dependency if the standard-library subset proves insufficient — superseding **ADR-0003**.
5. Calibrate routing thresholds from real task data — accumulates from P1, first reviewed at **G2**.
6. Implement `verify.py`, `integrate.py`, `routing.py` — **P1 entry**.
7. Transcribe G1 criteria — **G0 exit**.
8. Upgrade the approval seal to asymmetric signing if a pinned dependency becomes acceptable — superseding **ADR-0005**.

Item 5 from the design review — "validate the exact root-Git migration procedure in the P0 implementation plan before moving any repository metadata" — is now **closed**: the procedure was executed against a fixture and three defects were found and corrected.

## 11. Structural verification evidence

```text
PASS  plan_lines=2384  tasks=20  checkbox_steps=126
      backtick_fences=182 (balanced)  tilde_fences=2 (balanced)
      bash_blocks=57  without_set_euo_pipefail=0
      G0_criteria_covered=14/14  spec_§57_steps_covered=15/15
      app_files_modified=0
```

## 12. Verdict

**Revision 1: not executable.** Four defects would have stopped execution outright rather than degraded it — the bundle-verify abort at command two, the backup-path custody failure, the impossible retrospective contracts, and a dry run that could not run in its stated order with three of its inputs missing. Two more would have produced a *false pass*: gate closure by self-assertion, and verification blocks that print `ok` after failed checks.

**Revision 2: ready for execution.** No unresolved Critical or Important findings. One Minor residual (M-09), documented and made non-fatal, with an unambiguous authoritative check standing beside it.

The plan remains a planning artifact. No CashBooks application code has been written or changed.
