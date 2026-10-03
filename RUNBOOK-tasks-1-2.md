> **Authority: Historical (2026-09-30).** Migration-era operator runbook retained as evidence of the root-repository bootstrap.
> The active authority order is defined in [docs/DOCUMENT_AUTHORITY.md](docs/DOCUMENT_AUTHORITY.md).
> Nothing in this document authorizes work. Treat it as input, not instruction.

# CashBooks P0 — Runbook for Tasks 1 and 2

Run every block in **Terminal.app on your Mac**, from `~/cashguard`. Paste the output back
after each checkpoint (marked **↩︎ PASTE BACK**) before continuing.

Every block is safe to re-run except where noted. Blocks open with `set -euo pipefail`,
so a block that reaches its final `ok` line has passed every check above it.

---

## Step 0 — Backup (not in the plan; do it anyway)

Nothing in `~/cashguard` is version-controlled yet. Task 2 creates the repo. Until then
your six design documents exist in exactly one place.

```bash
cd ~/cashguard
tar --exclude='docs/pics' --exclude='*.pdf' \
    -czf ~/cashguard-predmigration-$(date +%Y%m%d-%H%M).tar.gz .
ls -lh ~/cashguard-predmigration-*.tar.gz
```

Expected: one archive, a few hundred KB to a few MB (the PDFs and `docs/pics` are excluded
— they are research inputs you can re-download, and they'd add ~180 MB).

**↩︎ PASTE BACK** the `ls -lh` line.

---

## Task 1 — Preflight

### 1.1 Toolchain and working directory

```bash
set -euo pipefail
cd ~/cashguard
pwd
test ! -e .git
git --version
python3 --version
rg --version | head -1
python3 - <<'PY'
import subprocess, sys
v = subprocess.check_output(["git","--version"]).split()[2].decode().split(".")
assert (int(v[0]), int(v[1])) >= (2, 28), f"git >= 2.28 required for 'git init -b'; found {'.'.join(v)}"
assert sys.version_info >= (3, 12), f"python3 >= 3.12 required; found {sys.version.split()[0]}"
print("toolchain: ok")
PY
```

**Two likely stumbles here, both fixable in one command:**

- **`python3 --version` shows 3.9.x** — macOS ships an old system Python. Install a current
  one and make sure it's first on `PATH`:
  ```bash
  brew install python@3.13
  # then confirm: python3 --version
  ```
  Do not lower the plan's floor. `agentctl` uses `datetime.UTC` (3.11+) and modern typing.
- **`rg: command not found`** — several verification blocks depend on it:
  ```bash
  brew install ripgrep
  ```

If `test ! -e .git` fails, a repository already exists at the root. **Stop** — that means
Task 2 has already run, and re-running it would be destructive. Tell me instead.

**↩︎ PASTE BACK** everything, including the versions.

### 1.2 Nested repository state

```bash
set -euo pipefail
cd ~/cashguard
git -C AppleBooks rev-parse HEAD
git -C AppleBooks branch --show-current
git -C AppleBooks status --short
test "$(git -C AppleBooks status --porcelain --untracked-files=no)" = "" \
  || { echo "FATAL: nested tree has tracked modifications; commit or stash inside AppleBooks/ first"; exit 1; }
echo "nested repo clean: ok"
```

Expected, as of 2026-09-01:

```text
441a6f877409c6abf55a8afdb616b9dd51c528e6
main
?? AppleBooks.xcodeproj/xcuserdata/
nested repo clean: ok
```

A different HEAD is **not** a failure — it just means you committed something since. Paste it
and I'll substitute it everywhere. A *dirty* tree is a stop: commit or stash inside
`AppleBooks/` first.

### 1.3 Canonical spec and supersession banners

```bash
set -euo pipefail
cd ~/cashguard
test -f docs/superpowers/specs/2026-08-31-cashbooks-multi-model-agent-system-design.md
rg -q '^> \*\*Authority notice \(2026-08-31\):\*\*' docs/superpowers/specs/2026-08-30-cashbooks-control-plane-design.md
rg -q '^> \*\*SUPERSEDED — DO NOT EXECUTE \(2026-08-31\):\*\*' docs/superpowers/plans/2026-08-31-cashbooks-control-plane.md
echo "authority preconditions: ok"
```

### 1.4 Write the preflight evidence file

```bash
set -euo pipefail
cd ~/cashguard
python3 - <<'PY'
import json, subprocess, datetime, pathlib
def sh(*a): return subprocess.check_output(a, text=True).strip()
doc = {
  "artifact_type": "preflight",
  "recorded_at": datetime.datetime.now(datetime.UTC).isoformat(),
  "repository_root": sh("pwd"),
  "git_version": sh("git", "--version"),
  "python3_version": sh("python3", "--version"),
  "rg_present": True,
  "root_was_a_repository": False,
  "nested_head": sh("git", "-C", "AppleBooks", "rev-parse", "HEAD"),
  "nested_branch": sh("git", "-C", "AppleBooks", "branch", "--show-current"),
  "nested_tracked_clean": sh("git","-C","AppleBooks","status","--porcelain","--untracked-files=no") == "",
  "nested_untracked": sh("git","-C","AppleBooks","status","--porcelain").splitlines(),
}
pathlib.Path("P0-PREFLIGHT.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
print("preflight recorded:", doc["nested_head"])
PY
cat P0-PREFLIGHT.json
```

**↩︎ PASTE BACK** the JSON. **Stop here.** Task 2 moves Git metadata; I want to see
Task 1's output before you start it.

---

## Task 2 — Repository consolidation

> Everything below is one continuous operation. If any block fails, **stop and paste the
> error** rather than improvising — three of this task's steps were wrong in revision 1 and
> the failure modes are subtle.

### 2.1 Bundle the history

```bash
set -euo pipefail
cd ~/cashguard
CB_BACKUP="$HOME/cashbooks-migration-backup-2026-09-01"
mkdir -p "$CB_BACKUP"
echo "backup directory: $CB_BACKUP"
git -C AppleBooks bundle create "$CB_BACKUP/applebooks.bundle" --all
git -C AppleBooks bundle verify "$CB_BACKUP/applebooks.bundle"
shasum -a 256 "$CB_BACKUP/applebooks.bundle"
```

Expected: `… is okay`, **two** refs listed (`refs/heads/main` and `HEAD`), and
"The bundle records a complete history."

The `git -C AppleBooks` prefix is load-bearing: `git bundle verify` fails with
*"need a repository to verify a bundle"* when run from a non-repo directory, which
`~/cashguard` still is.

**↩︎ PASTE BACK.**

### 2.2 Move the nested tree to the backup

```bash
set -euo pipefail
cd ~/cashguard
CB_BACKUP="$HOME/cashbooks-migration-backup-2026-09-01"
test -d "$CB_BACKUP" || { echo "FATAL: backup directory missing; do not proceed"; exit 1; }
NESTED_HEAD="$(python3 -c "import json;print(json.load(open('P0-PREFLIGHT.json'))['nested_head'])")"
mv AppleBooks "$CB_BACKUP/AppleBooks-working-tree"
test ! -e AppleBooks
test -d "$CB_BACKUP/AppleBooks-working-tree/.git"
test "$(git -C "$CB_BACKUP/AppleBooks-working-tree" rev-parse HEAD)" = "$NESTED_HEAD"
echo "nested tree moved and identity confirmed: ok"
```

This is the one irreversible-feeling moment: your app source now lives only in
`~/cashbooks-migration-backup-2026-09-01/`. It is a move, not a delete — plus you have the
Step 0 tarball and the bundle. Step 2.6 puts it back.

### 2.3 Initialize the root repository

```bash
set -euo pipefail
cd ~/cashguard
git init -b main
test "$(git rev-parse --show-toplevel)" = "$(pwd -P)"
echo "root repository: ok"
```

### 2.4 `.gitignore`

Create `~/cashguard/.gitignore` with exactly the content in **Task 2 Step 4** of the plan.
Fastest path — copy it straight out of the plan file:

```bash
set -euo pipefail
cd ~/cashguard
python3 - <<'PY'
import re, pathlib
plan = pathlib.Path("docs/superpowers/plans/2026-09-01-cashbooks-p0-control-plane-bootstrap.md").read_text()
block = re.search(r"```gitignore\n(.*?)```", plan, re.S).group(1)
pathlib.Path(".gitignore").write_text(block)
print(block)
PY
```

Expected: the ignore rules print, ending with the Python tooling section.

### 2.5 First commit

```bash
set -euo pipefail
cd ~/cashguard
git add .gitignore P0-PREFLIGHT.json BOOTSTRAP.md CLAUDE.md DESIGN_SPEC.md \
        INFRA_PLAN.md TASKS.md TECH_SPEC.md docs
test -z "$(git ls-files 'docs/*.pdf' 'docs/pics/*' 'docs/ach.md' 'docs/ebook.md' 'docs/money1.md' 'docs/silo.md' 'docs/22365_19_Agents_v8.md')"
git commit -m "chore: establish CashBooks repository root

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0183n79DKHi8SvUaC9VeKDRu"
git show --stat --oneline HEAD | head -30
```

The `test -z` line is the check that ~180 MB of research PDFs and page images stayed out.
If it prints paths, the `.gitignore` didn't take — stop.

**↩︎ PASTE BACK** the commit stat.

### 2.6 Import the Xcode history

```bash
set -euo pipefail
cd ~/cashguard
CB_BACKUP="$HOME/cashbooks-migration-backup-2026-09-01"
test ! -f .git/MERGE_HEAD || { echo "FATAL: repository is mid-merge; run 'git merge --abort' first"; exit 1; }
NESTED_HEAD="$(python3 -c "import json;print(json.load(open('P0-PREFLIGHT.json'))['nested_head'])")"
git remote add applebooks-history "$CB_BACKUP/applebooks.bundle"
git fetch applebooks-history main
git merge -s ours --no-commit --allow-unrelated-histories applebooks-history/main
git read-tree --prefix=AppleBooks/ -u applebooks-history/main
git commit -m "chore: import AppleBooks Xcode history under AppleBooks/

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0183n79DKHi8SvUaC9VeKDRu"
git remote remove applebooks-history
test -d AppleBooks
test "$(git rev-parse HEAD^2)" = "$NESTED_HEAD"
echo "import: ok"
```

> **If anything in this block fails**, run `git merge --abort`, confirm `git status` is clean,
> and re-run the whole block from `git remote add`. **Never run `git commit` while
> `.git/MERGE_HEAD` exists** unless `git read-tree` succeeded in the same block — I reproduced
> the consequence: a later commit messaged *"docs: establish document authority"* silently
> acquired two parents and became the history-import merge, with `AppleBooks/` missing from
> the tree, and the reachability check still passed.

**↩︎ PASTE BACK.**

### 2.7 Restore your Xcode user state

```bash
set -euo pipefail
cd ~/cashguard
CB_BACKUP="$HOME/cashbooks-migration-backup-2026-09-01"
if [ -d "$CB_BACKUP/AppleBooks-working-tree/AppleBooks.xcodeproj/xcuserdata" ]; then
  cp -R "$CB_BACKUP/AppleBooks-working-tree/AppleBooks.xcodeproj/xcuserdata" AppleBooks/AppleBooks.xcodeproj/
fi
test ! -d AppleBooks/.git
test -z "$(git status --porcelain)"
echo "worktree restored and clean: ok"
```

### 2.8 Prove no application code changed

```bash
set -euo pipefail
cd ~/cashguard
CB_BACKUP="$HOME/cashbooks-migration-backup-2026-09-01"
NESTED_HEAD="$(python3 -c "import json;print(json.load(open('P0-PREFLIGHT.json'))['nested_head'])")"
test "$(git rev-parse "$NESTED_HEAD^{tree}")" = "$(git rev-parse HEAD:AppleBooks)"
echo "AppleBooks subtree byte-identical to imported commit: ok"
diff -qr -x .git -x xcuserdata -x '.DS_Store' -x '*.xcuserstate' -x DerivedData -x .build \
  "$CB_BACKUP/AppleBooks-working-tree" AppleBooks \
  && echo "working-tree comparison clean: ok" \
  || echo "NOTE: review the differences above — only ignored or untracked files may appear"
```

The tree-hash equality is the real proof and it is exact. The `diff -qr` is a secondary
human check and is deliberately non-fatal — a stray `.DS_Store` there is noise, not a failure.

### 2.9 Prove the original history is reachable

```bash
set -euo pipefail
cd ~/cashguard
NESTED_HEAD="$(python3 -c "import json;print(json.load(open('P0-PREFLIGHT.json'))['nested_head'])")"
git rev-parse "$NESTED_HEAD^{commit}" >/dev/null
git merge-base --is-ancestor "$NESTED_HEAD" HEAD
git log --oneline --all | rg -q 'Initial Commit'
git log --graph --oneline --decorate -6
echo "history reachable: ok"
```

Expected: the graph shows the import commit with two parents, the second being your
`Initial Commit`.

### 2.10 Record migration evidence and commit

```bash
set -euo pipefail
cd ~/cashguard
python3 - <<'PY'
import json, subprocess, datetime, pathlib, os
def sh(*a): return subprocess.check_output(a, text=True).strip()
nested = json.load(open("P0-PREFLIGHT.json"))["nested_head"]
bak = os.path.expanduser("~/cashbooks-migration-backup-2026-09-01")
doc = {
  "artifact_type": "migration",
  "recorded_at": datetime.datetime.now(datetime.UTC).isoformat(),
  "nested_head": nested,
  "import_commit": sh("git", "rev-parse", "HEAD"),
  "import_second_parent": sh("git", "rev-parse", "HEAD^2"),
  "applebooks_subtree": sh("git", "rev-parse", "HEAD:AppleBooks"),
  "imported_tree": sh("git", "rev-parse", nested + "^{tree}"),
  "subtree_identical": sh("git","rev-parse","HEAD:AppleBooks") == sh("git","rev-parse",nested+"^{tree}"),
  "nested_git_removed": not pathlib.Path("AppleBooks/.git").exists(),
  "recoverable_bundle": f"{bak}/applebooks.bundle",
  "bundle_sha256": sh("shasum","-a","256",f"{bak}/applebooks.bundle").split()[0],
}
assert doc["subtree_identical"], "subtree diverged"
pathlib.Path("P0-MIGRATION.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
print(json.dumps(doc, indent=2, sort_keys=True))
PY
git add P0-MIGRATION.json
git commit -m "chore: record repository migration evidence

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0183n79DKHi8SvUaC9VeKDRu"
echo "migration evidence: ok"
```

**↩︎ PASTE BACK** the JSON.

---

## After Task 2

- Keep `~/cashbooks-migration-backup-2026-09-01/` until G0 closes. It holds the bundle and
  your pre-migration working tree.
- Open the Xcode project once (`open AppleBooks/AppleBooks.xcodeproj`) and confirm it still
  builds. Nothing should have changed — the tree hash proves it — but seeing it build is
  worth thirty seconds.
- Task 3 (document authority) is next. It is additive and low-risk: no Git metadata moves,
  and every change is a new file or a prepended banner.
