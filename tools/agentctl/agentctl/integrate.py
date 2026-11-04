"""Serialized candidate integration: verify, plan in Git, gate, claim, advance, record.

Protocol (frozen by the coordinator; see ``integration_queue`` for the state side):

1. Reconcile any task found in ``integrating`` against actual Git state.
2. Select the queue head (or accept an explicit id only if it *is* the head).
3. Verify every binding against stored evidence and actual Git (task stays
   ``integration_ready``).
4. In a DETACHED worktree at the integration head: fast-forward or
   ``git merge --no-ff`` the UNCHANGED candidate commit, then rerun the
   contract's deterministic checks on the resulting tree. Any failure is a
   non-advancing, recorded rejection.
5. Transaction 1: ``integration-intent`` + ``integration-lease`` +
   ``integration_lease_issued`` (``integration_ready -> integrating``).
6. ``git update-ref`` compare-and-swap of the integration branch.
7. Transaction 2: ``integrating -> completed`` + ``integration-record``
   (a schema-valid integration manifest).

Git and SQLite are not one atomic transaction. Steps 5-7 are therefore ordered
so that a crash between any two of them is recoverable from actual Git state
(``reconcile``), and the candidate commit is never rebased, edited, or resolved.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
import weakref
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .check_environment import (
    ISOLATION_LEGACY,
    CandidateTreeUnreadable,
    CandidateTreeUnsafe,
    CheckEnvironmentUnavailable,
    legacy_environment,
    materialize_tree,
    require_sandbox,
    run_candidate_checks,
    selected_isolation,
)
from .errors import PlanningError
from .events import make_event
from .hashing import sha256_hex, sha256_of_json
from .integration_queue import (
    IntegrationBusy,
    IntegrationQueue,
    IntegrationRefused,
    IntegrationRejected,
    build_queue,
    claim_integration,
    complete_integration,
    current_lease,
    latest_intent,
    lease_is_live,
    new_lease,
    next_lease_epoch,
    record_reconciliation,
    record_rejection,
    run_gate_commands,
    store_query,
)
from .interfaces import current_interface_binding
from .locks import semantic_lock_claims
from .models import TaskState
from .scheduler import NO_DETERMINISTIC_CHECKS, effective_checks, gate_risk_refusal
from .store import EventStore
from .validate import validate_artifact

__all__ = [
    "DEFAULT_BRANCH",
    "IntegrationSettings",
    "exit_code_for",
    "integrate",
    "integrate_task",
]

DEFAULT_BRANCH = "agentctl/integration"
_PROTECTED_BRANCHES = frozenset({"main", "master"})
_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ZERO = "0" * 40
_OK_OUTCOMES = frozenset({"integrated", "already_integrated", "reconciled"})
_DRIVER_KEY = re.compile(
    r"^(filter\..+\.(clean|smudge|process)|merge\..+\.driver|diff\..+\.(command|textconv))$"
)

FaultHook = Callable[[str], None]


@dataclass(frozen=True)
class IntegrationSettings:
    branch: str = DEFAULT_BRANCH
    #: Commit to create the integration branch from when absent (default: the
    #: candidate's own contract base commit).
    base: str | None = None
    #: Control-plane run id of this invocation. A deterministic integrator has no
    #: model session, so a UUID is minted per invocation unless set explicitly.
    integrator_id: str | None = None
    check_timeout: float = 600.0
    lock_timeout: float = 300.0


def exit_code_for(result: Mapping[str, Any]) -> int:
    if result.get("outcome") in _OK_OUTCOMES:
        return 0
    if result.get("outcome") == "queue_empty":
        return 3
    return 1


# ------------------------------------------------------------------ git ----


class _Git:
    """Real ``git`` subprocesses that cannot be redirected or made to run planted code.

    Repository configuration is untrusted input: the candidate's own verification
    (not this module) runs commands in a worktree that shares ``.git`` with this
    repository, so ``diff.external``, ``core.fsmonitor`` and friends may have been
    planted. Every invocation therefore overrides the known code-execution
    settings on the command line (which outranks repository config), disables
    pagers and hooks, ignores global and system config, and runs with an
    allow-listed environment. Diff-style callers also pass ``--no-ext-diff
    --no-textconv``.
    """

    _HARDENING = (
        "--no-pager",
        "-c", "core.fsmonitor=false",
        "-c", "core.pager=cat",
        "-c", "diff.external=",
        "-c", "core.hooksPath=/dev/null",
        "-c", "core.attributesFile=/dev/null",
        "-c", "commit.gpgsign=false",
        "-c", "gc.auto=0",
        "-c", "maintenance.auto=false",
    )

    def __init__(self, root: Path) -> None:
        self.root = root
        self._home = tempfile.mkdtemp(prefix="agentctl-git-home-")
        weakref.finalize(self, shutil.rmtree, self._home, True)
        #: Driver keys configured when this integrator started (``guard_drivers``).
        self.startup_drivers = self.drivers()

    def drivers(self, cwd: str | os.PathLike[str] | None = None) -> dict[str, str]:
        """Code-running driver keys in repository (local/worktree) configuration.

        ``filter.<n>.clean|smudge|process``, ``merge.<n>.driver`` and
        ``diff.<n>.command|textconv``. Command-line scope (our own overrides) is ignored.
        """
        done = subprocess.run(
            ["git", *self._HARDENING, "config", "-z", "--show-scope", "--get-regexp",
             r"^(filter|merge|diff)\."],
            cwd=cwd or self.root, env=self._base_env(), capture_output=True, check=False,
        )
        found: dict[str, str] = {}
        fields = done.stdout.split(b"\0")
        for scope, entry in zip(fields[0::2], fields[1::2]):
            if scope == b"command":
                continue
            key, _, value = os.fsdecode(entry).partition("\n")
            if _DRIVER_KEY.match(key):
                found[key] = value
        return found

    def guard_drivers(self, cwd: str | os.PathLike[str] | None = None) -> None:
        """Refuse when a driver key appeared or changed since this integrator started."""
        current = self.drivers(cwd)
        new = sorted(k for k, v in current.items() if self.startup_drivers.get(k) != v)
        if new:
            raise IntegrationRefused(
                "a filter/merge/diff driver was configured after the integrator started",
                reason_code="gate_tampered_repository", driver_keys=new[:20],
            )

    def _neutralised_drivers(self, cwd: str | os.PathLike[str] | None) -> dict[str, str]:
        """Environment overrides that disable every configured driver for one git call.

        Command-line scope outranks repository configuration, and an empty filter
        command means "no filter" (verified against git 2.43). A merge driver is
        replaced by ``false`` so that, if one were ever reached, the merge fails
        (conflict) instead of being resolved by planted code.
        """
        overrides: dict[str, str] = {}
        for key in {**self.startup_drivers, **self.drivers(cwd)}:
            section, _, rest = key.partition(".")
            name = rest.rsplit(".", 1)[0]
            if section == "filter":
                for variable in ("clean", "smudge", "process"):
                    overrides[f"filter.{name}.{variable}"] = ""
                overrides[f"filter.{name}.required"] = "false"
            elif section == "merge":
                overrides[f"merge.{name}.driver"] = "false"
            else:
                overrides[f"diff.{name}.command"] = ""
                overrides[f"diff.{name}.textconv"] = ""
        env = {"GIT_CONFIG_COUNT": str(len(overrides))}
        for index, (key, value) in enumerate(sorted(overrides.items())):
            env[f"GIT_CONFIG_KEY_{index}"] = key
            env[f"GIT_CONFIG_VALUE_{index}"] = value
        return env

    def _env(
        self, extra: Mapping[str, str] | None = None, cwd: str | os.PathLike[str] | None = None
    ) -> dict[str, str]:
        env = self._base_env()
        env.update(self._neutralised_drivers(cwd))
        env.update(extra or {})
        return env

    def _base_env(self) -> dict[str, str]:
        return {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": self._home,
            "LC_ALL": "C",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
            # refs/replace/* and grafts would let planted refs substitute objects.
            "GIT_NO_REPLACE_OBJECTS": "1",
        }

    def command(self, *args: str) -> list[str]:
        return ["git", *self._HARDENING, *args]

    def run(
        self,
        *args: str,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        text: bool = True,
        stdin: bytes | None = None,
    ) -> subprocess.CompletedProcess[Any]:
        return subprocess.run(
            self.command(*args),
            cwd=cwd or self.root,
            env=self._env(env, cwd),
            capture_output=True,
            text=text,
            input=stdin,
            check=False,
        )

    def popen(self, *args: str, cwd: str | os.PathLike[str] | None = None) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            self.command(*args),
            cwd=cwd or self.root,
            env=self._env(None, cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def out(self, *args: str, cwd: str | os.PathLike[str] | None = None) -> str | None:
        done = self.run(*args, cwd=cwd)
        return done.stdout.strip() if done.returncode == 0 else None

    def ok(self, *args: str, cwd: str | os.PathLike[str] | None = None) -> bool:
        return self.run(*args, cwd=cwd).returncode == 0

    def branch_head(self, branch: str) -> str | None:
        return self.out("rev-parse", "--verify", "-q", f"refs/heads/{branch}^{{commit}}")

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        return self.ok("merge-base", "--is-ancestor", ancestor, descendant)

    def diff_hash(self, base: str, result: str) -> str | None:
        done = self.run(
            "diff", "--binary", "--full-index", "--no-ext-diff", "--no-textconv",
            base, result, text=False,
        )
        return sha256_hex(done.stdout) if done.returncode == 0 else None

    def changed_paths(self, older: str, newer: str, *, cwd: str | os.PathLike[str] | None = None) -> set[str]:
        """Paths whose content or mode differs between two commits (renames not detected)."""
        done = self.run(
            "diff", "--name-only", "-z", "--no-renames", "--no-ext-diff", "--no-textconv",
            older, newer, cwd=cwd, text=False,
        )
        if done.returncode != 0:
            raise IntegrationRefused("cannot diff commits", reason_code="plan_unreadable")
        return {os.fsdecode(name) for name in done.stdout.split(b"\0") if name}

    def first_parent_chain(self, head: str) -> set[str]:
        out = self.out("rev-list", "--first-parent", head)
        return set(out.split()) if out else set()

    def validate_branch(self, branch: str) -> None:
        def refuse(why: str) -> IntegrationRefused:
            return IntegrationRefused(
                f"integration branch is not usable: {why}",
                reason_code="integration_branch_invalid",
                branch=branch,
            )

        if branch in _PROTECTED_BRANCHES or not self.ok("check-ref-format", "--branch", branch):
            raise refuse("protected or malformed name")
        if self.out("symbolic-ref", "-q", "--short", "HEAD") == branch:
            raise refuse("it is the branch checked out in the repository")
        listing = self.out("worktree", "list", "--porcelain") or ""
        if f"branch refs/heads/{branch}" in listing.splitlines():
            raise refuse("it is checked out in a worktree")

    def compare_and_swap(self, branch: str, new: str, old: str | None, note: str) -> bool:
        done = self.run("update-ref", "-m", note, f"refs/heads/{branch}", new, old or _ZERO)
        return done.returncode == 0

    def remove_worktree(self, path: Path) -> None:
        if path.exists():
            self.run("worktree", "remove", "--force", str(path))
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
        self.run("worktree", "prune")


_PROTECTED_FILES = ("config", "config.worktree", "HEAD", "commondir", "shallow")
_PROTECTED_DIRECTORIES = ("info", "hooks", "worktrees", "objects/info")


def _common_dir(git: _Git) -> Path:
    common = git.out("rev-parse", "--git-common-dir")
    if common is None:
        raise IntegrationRefused("cannot locate the repository", reason_code="plan_unreadable")
    path = Path(common)
    return (path if path.is_absolute() else git.root / path).resolve()


def _protected_state(git: _Git, branch: str, exclude: Path | None = None) -> dict[str, str]:
    """Hashes of repository metadata a gate must never change.

    Configuration, ``info/*`` (attributes, grafts, excludes), hooks, ``HEAD``, every
    worktree's metadata, ``objects/info`` (alternates), refs under the integration
    branch, ``refs/replace/*``, and the effective configuration as git reads it
    (which covers included files). ``exclude`` is the integrator's own detached
    worktree metadata, which is removed after planning.
    """
    common = _common_dir(git)
    state: dict[str, str] = {}

    def record(path: Path) -> None:
        name = path.relative_to(common).as_posix()
        if path.is_symlink():
            state[name] = "link:" + os.readlink(path)
        elif path.is_dir():
            state[name] = "dir"
        elif path.is_file():
            state[name] = sha256_hex(path.read_bytes())
        else:
            state[name] = "other"

    for name in _PROTECTED_FILES:
        if os.path.lexists(common / name):
            record(common / name)
    for name in _PROTECTED_DIRECTORIES:
        top = common / name
        if not os.path.lexists(top):
            continue
        record(top)
        for directory, subdirectories, files in os.walk(top):
            here = Path(directory)
            if exclude is not None and (here == exclude or exclude in here.parents):
                subdirectories[:] = []
                continue
            for entry in [*subdirectories, *files]:
                if exclude is not None and here / entry == exclude:
                    continue
                record(here / entry)
    refs = git.run(
        "for-each-ref", "--format=%(refname) %(objectname)",
        "refs/replace/", f"refs/heads/{branch}", f"refs/heads/{branch}/",
    )
    for line in refs.stdout.splitlines():
        name, _, oid = line.partition(" ")
        state[f"refs:{name}"] = oid
    effective = git.run("config", "--list", "-z", "--show-origin", "--show-scope", text=False)
    state["config:effective"] = sha256_hex(effective.stdout)
    return state


def _changed(before: Mapping[str, str], after: Mapping[str, str]) -> list[str]:
    return sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))


def _unsafe(path: str, why: str) -> IntegrationRejected:
    return _reject(
        "candidate_tree_unsafe", "human_required",
        "the planned tree contains a path that cannot be materialized safely",
        path=path, why=why,
    )


def _materialize_tree(git: _Git, commit: str, destination: Path) -> None:
    """Write the exact tree of ``commit`` to ``destination`` (see :func:`materialize_tree`).

    The materialiser is shared with ``verify`` through the check-environment contract; here its refusals
    become the integrator's recorded rejection (``candidate_tree_unsafe``) or refusal (``plan_unreadable``).
    """
    try:
        materialize_tree(git, commit, destination)
    except CandidateTreeUnsafe as exc:
        raise _unsafe(exc.path, exc.why) from exc
    except CandidateTreeUnreadable as exc:
        raise IntegrationRefused("cannot read the planned tree", **exc.context) from exc


def _isolation() -> str:
    """The selected check isolation, as an integration refusal when the selector is not recognised."""
    try:
        return selected_isolation()
    except CheckEnvironmentUnavailable as exc:
        raise _refusal(exc) from exc


def _refusal(exc: CheckEnvironmentUnavailable) -> IntegrationRefused:
    details = {key: value for key, value in exc.context.items() if key != "reason_code"}
    return IntegrationRefused(exc.message, reason_code=exc.reason_code, **details)


# ------------------------------------------------------------- evidence ----


@dataclass(frozen=True)
class _Evidence:
    task_id: str
    attempt_id: str
    base_commit: str
    result_commit: str
    tree_hash: str
    diff_hash: str
    contract_hash: str
    context_hash: str
    verification_hash: str
    review_id: str
    review_payload: Mapping[str, Any]
    implementation_manifest: Mapping[str, Any]
    interface_hashes: tuple[tuple[str, str], ...]
    checks: tuple[str, ...]


def _reject(reason: str, outcome: str, message: str, **context: object) -> IntegrationRejected:
    return IntegrationRejected(message, reason_code=reason, outcome=outcome, **context)


def _verify(store: EventStore, git: _Git, task_id: str, integrator_id: str) -> _Evidence:
    """Verify every binding against stored evidence and actual Git.

    Nothing here trusts a stored flag without recomputing it from its source.
    """
    root = store.repository_root
    task = store.rows("SELECT * FROM tasks WHERE task_id = ?", (task_id,))[0]
    payload = json.loads(task["payload_json"])

    # (0) Pre-claim refusals read the contract, not a stored pass: high/critical (and unknown) risk is refused
    # under every isolation until the sandbox boundary has had a further independent review and an owner
    # decision, and a contract with no checks verified nothing. Whether the sandbox is available is checked where
    # the gates are about to run (`_plan`), so crash recovery that runs no gate does not need it.
    _isolation()  # an unrecognised selector is refused, never ignored
    refusal = gate_risk_refusal(payload.get("risk"))
    if refusal is not None:
        raise _reject(
            refusal, "human_required",
            "gates are allowed only for low and medium risk tasks until the sandbox boundary has had a further "
            "independent review and an owner decision",
            retry_requires_new_evidence=True, risk=str(payload.get("risk"))[:40],
        )
    if not effective_checks(payload.get("deterministic_checks")):
        raise _reject(
            NO_DETERMINISTIC_CHECKS, "repair_required",
            "the task contract owns no deterministic check, so nothing was verified",
            retry_requires_new_evidence=True,
        )

    # (a) required dependencies are completed.
    waiting = [
        row["depends_on_task_id"]
        for row in store.rows(
            "SELECT d.depends_on_task_id FROM dependencies d LEFT JOIN tasks t "
            "ON t.task_id = d.depends_on_task_id WHERE d.task_id = ? "
            "AND d.depends_on_task_id != '' AND (t.state IS NULL OR t.state != 'completed') "
            "ORDER BY d.depends_on_task_id",
            (task_id,),
        )
    ]
    if waiting:
        raise _reject(
            "dependency_incomplete", "waiting_on_dependency",
            "required dependencies are not completed", waiting_on=waiting,
        )

    # (b) interface bindings are current; locked sources are unchanged.
    pins = payload.get("interface_pins") or {}
    interface_hashes: list[tuple[str, str]] = []
    for row in store.rows(
        "SELECT interface_id, interface_hash FROM dependencies "
        "WHERE task_id = ? AND interface_id != '' ORDER BY interface_id",
        (task_id,),
    ):
        interface_id = row["interface_id"]
        try:
            binding = current_interface_binding(root, interface_id, store)
        except PlanningError as exc:
            raise _reject(
                "interface_binding_unverifiable", "replan_required",
                "current interface binding cannot be established",
                interface=interface_id, error=str(exc),
            ) from exc
        if binding.content_hash != row["interface_hash"] or (
            interface_id in pins and binding.version != pins[interface_id]
        ):
            raise _reject(
                "interface_binding_stale", "replan_required",
                "candidate was built against a superseded interface binding",
                state_event="dependency_changed", state_target=TaskState.STALE,
                interface=interface_id, recorded_hash=row["interface_hash"],
                current_hash=binding.content_hash, pinned_version=pins.get(interface_id),
                current_version=binding.version,
            )
        interface_hashes.append((f"{interface_id}@{binding.version}", binding.content_hash))
    recorded_locks = dict(payload.get("semantic_locks") or {})
    try:
        current_locks = semantic_lock_claims(recorded_locks.keys(), root)
    except PlanningError as exc:
        raise _reject(
            "semantic_lock_unverifiable", "replan_required",
            "semantic lock sources cannot be established", error=str(exc),
        ) from exc
    if current_locks != recorded_locks:
        raise _reject(
            "semantic_lock_stale", "replan_required",
            "a semantic lock contract changed after the candidate was built",
            recorded=recorded_locks, current=current_locks,
        )

    # (c)/(f) the candidate is the exact commit that was verified and reviewed.
    attempt_rows = store.rows(
        "SELECT * FROM attempts WHERE task_id = ? ORDER BY attempt_number DESC LIMIT 1",
        (task_id,),
    )
    if not attempt_rows or not attempt_rows[0]["result_commit"]:
        raise _reject("candidate_evidence_missing", "repair_required", "task has no candidate attempt")
    attempt = attempt_rows[0]
    attempt_id = str(attempt["attempt_id"])
    binding_rows = store.rows(
        "SELECT payload_json FROM artifacts WHERE artifact_id = ? AND integrable = 1",
        (f"candidate-binding:{task_id}:{attempt_id}",),
    )
    manifest_rows = store.rows(
        "SELECT payload_json FROM artifacts WHERE task_id = ? AND attempt_id = ? "
        "AND artifact_type = 'implementation-manifest' AND integrable = 1 LIMIT 1",
        (task_id, attempt_id),
    )
    if not binding_rows or not manifest_rows:
        raise _reject(
            "candidate_evidence_missing", "repair_required",
            "candidate binding or implementation manifest is missing", attempt_id=attempt_id,
        )
    binding = json.loads(binding_rows[0]["payload_json"])
    manifest = json.loads(manifest_rows[0]["payload_json"])
    result = str(binding["result_commit"])
    base = str(binding["base_commit"])
    if (
        binding.get("task_id") != task_id
        or binding.get("attempt_id") != attempt_id
        or result != attempt["result_commit"]
        or manifest.get("result_commit") != result
        or base != task["base_commit"]
    ):
        raise _reject(
            "candidate_binding_mismatch", "repair_required",
            "stored candidate identity disagrees across artifacts", attempt_id=attempt_id,
        )
    if binding.get("contract_hash") != task["contract_hash"]:
        raise _reject(
            "contract_stale", "replan_required", "candidate contract hash is not current",
            recorded=binding.get("contract_hash"), current=task["contract_hash"],
        )
    if binding.get("context_hash") != task["context_hash"]:
        raise _reject(
            "context_stale", "replan_required", "candidate context hash is not current",
            recorded=binding.get("context_hash"), current=task["context_hash"],
        )
    if not git.ok("cat-file", "-e", f"{result}^{{commit}}") or not git.is_ancestor(base, result):
        raise _reject(
            "candidate_commit_missing", "repair_required",
            "candidate commit is absent or not a descendant of its base", result_commit=result,
        )
    tree = git.out("rev-parse", f"{result}^{{tree}}")
    diff = git.diff_hash(base, result)
    if tree != binding["tree_hash"] or diff != binding["diff_hash"]:
        raise _reject(
            "candidate_binding_mismatch", "repair_required",
            "actual candidate tree or diff differs from the verified binding",
            stored_tree=binding["tree_hash"], actual_tree=tree,
            stored_diff=binding["diff_hash"], actual_diff=diff,
        )
    worktree = (root / str(binding.get("worktree_path") or "")).resolve()
    if binding.get("worktree_path") and worktree.is_dir():
        head = git.out("rev-parse", "HEAD", cwd=worktree)
        dirty = git.run("diff", "--quiet", "--no-ext-diff", "HEAD", cwd=worktree).returncode != 0
        if head != result or dirty:
            raise _reject(
                "candidate_modified_after_review", "repair_required",
                "candidate worktree no longer holds the reviewed commit unchanged",
                reviewed=result, worktree_head=head, tracked_changes=dirty,
            )

    # (d) verification evidence is intact, passed, and what the review saw.
    evidence_rows = store.rows(
        "SELECT content_hash, payload_json, integrable FROM artifacts WHERE artifact_id = ?",
        (f"verification:{task_id}:{attempt_id}",),
    )
    if not evidence_rows:
        raise _reject(
            "verification_evidence_missing", "repair_required",
            "no stored verification evidence for the candidate", attempt_id=attempt_id,
        )
    evidence = json.loads(evidence_rows[0]["payload_json"])
    evidence_hash = str(evidence_rows[0]["content_hash"])
    if sha256_of_json(evidence) != evidence_hash or evidence.get("candidate_binding") != binding:
        raise _reject(
            "verification_evidence_mismatch", "repair_required",
            "stored verification evidence is not bound to this candidate",
        )
    if evidence.get("passed") is not True or evidence_rows[0]["integrable"] != 1:
        raise _reject(
            "verification_evidence_failed", "repair_required",
            "stored verification evidence is not a pass",
        )

    # (e) an independent, passing review bound to exactly this evidence.
    review_rows = store.rows(
        "SELECT * FROM reviews WHERE task_id = ? AND attempt_id = ? ORDER BY rowid DESC",
        (task_id, attempt_id),
    )
    if not review_rows:
        raise _reject(
            "review_missing", "review_required", "candidate has no independent review",
            attempt_id=attempt_id,
        )
    passing = [row for row in review_rows if row["verdict"] == "no_blocking_findings"]
    if not passing:
        raise _reject(
            "review_not_passing", "repair_required", "no review returned no_blocking_findings",
            verdicts=[row["verdict"] for row in review_rows],
        )
    review = passing[0]
    verdict = json.loads(review["payload_json"])
    if verdict.get("verdict") != "no_blocking_findings" or any(
        finding.get("blocking") is True for finding in verdict.get("findings", ())
    ):
        raise _reject(
            "review_not_passing", "repair_required", "review payload carries blocking findings",
            review_id=review["review_id"],
        )
    expected = {
        "reviewed_result_commit": result,
        "tree_hash": binding["tree_hash"],
        "diff_hash": binding["diff_hash"],
        "declared_base_commit": base,
        "contract_hash": task["contract_hash"],
        "context_hash": task["context_hash"],
        "verification_evidence_hash": evidence_hash,
    }
    drifted = {k: [v, verdict.get(k)] for k, v in expected.items() if verdict.get(k) != v}
    if drifted:
        raise _reject(
            "review_binding_mismatch", "review_required",
            "review was bound to different candidate evidence", drifted=sorted(drifted),
        )

    # Separation of duty, re-derived from stored identities (not trusted).
    lease_workers = [
        row["worker_identity"]
        for row in store.rows(
            "SELECT worker_identity FROM leases WHERE attempt_id = ?", (attempt_id,)
        )
    ]
    author_ids = {
        value
        for value in (attempt["author_session_id"], attempt["author_run_id"], *lease_workers)
        if value
    }
    reviewer_ids = {
        value
        for value in (
            review["reviewer_session_id"], review["reviewer_run_id"],
            verdict.get("reviewer_session_id"), verdict.get("reviewer_run_id"),
        )
        if value
    }
    if author_ids & reviewer_ids:
        raise _reject(
            "reviewer_not_independent", "review_required",
            "reviewer identity collides with the author identity",
        )
    if integrator_id in author_ids | reviewer_ids:
        raise _reject(
            "integrator_not_independent", "human_required",
            "integrator identity collides with the author or reviewer identity",
        )

    return _Evidence(
        task_id=task_id,
        attempt_id=attempt_id,
        base_commit=base,
        result_commit=result,
        tree_hash=str(tree),
        diff_hash=str(diff),
        contract_hash=str(task["contract_hash"]),
        context_hash=str(task["context_hash"]),
        verification_hash=evidence_hash,
        review_id=str(review["review_id"]),
        review_payload=verdict,
        implementation_manifest=manifest,
        interface_hashes=tuple(interface_hashes),
        checks=effective_checks(payload.get("deterministic_checks")),
    )


# ------------------------------------------------------------- planning ----


@dataclass(frozen=True)
class _Plan:
    strategy: str
    commit: str
    tree: str
    gates: list[dict[str, Any]]
    patch_unchanged: bool
    environment: dict[str, Any] | None = None


def _safe_component(value: str) -> str:
    """An id may become a path component only if it matches the task-id pattern."""
    if not isinstance(value, str) or not _TASK_ID.match(value) or ".." in value:
        raise IntegrationRefused(
            "identifier cannot be used as a path component",
            reason_code="task_unknown", identifier=str(value)[:80],
        )
    return value


def _integration_dir(root: Path, task_id: str) -> Path:
    return root / "agent-system" / "runtime" / "integration" / _safe_component(task_id)


def _plan(
    git: _Git,
    evidence: _Evidence,
    *,
    old_head: str,
    planned_at: int,
    integration_id: str,
    branch: str,
    worktree: Path,
    check_timeout: float,
) -> _Plan:
    """Compute the resulting commit and rerun the gates in a detached worktree.

    No ref is touched. The planned commit is reproducible: author, committer and
    dates are pinned from ``planned_at`` so recovery can re-derive the same hash.
    """
    candidate = evidence.result_commit
    isolation = _isolation()
    if isolation != ISOLATION_LEGACY:
        try:
            require_sandbox()  # fail closed: no plan, no gate, no state change without the sandbox
        except CheckEnvironmentUnavailable as exc:
            raise _refusal(exc) from exc
    if git.is_ancestor(candidate, old_head):
        raise _reject(
            "candidate_already_in_integration_branch", "human_required",
            "integration branch already contains the candidate commit", head=old_head,
        )
    if old_head != evidence.base_commit and not git.is_ancestor(evidence.base_commit, old_head):
        raise _reject(
            "base_incompatible", "replan_required",
            "candidate base is not in the integration branch history",
            base=evidence.base_commit, head=old_head,
        )
    touched: set[str] = set()
    if old_head != evidence.base_commit:
        # Merge attributes (``merge=union``, custom drivers) can resolve a real conflict
        # silently. Refuse to rely on the merge machinery for any path both sides changed:
        # for every path the candidate touched, the head must still match the merge base.
        merge_base = git.out("merge-base", old_head, candidate)
        if merge_base is None:
            raise _reject(
                "base_incompatible", "replan_required",
                "candidate and integration branch share no merge base", head=old_head,
            )
        touched = git.changed_paths(merge_base, candidate)
        overlap = sorted(touched & git.changed_paths(merge_base, old_head))
        if overlap:
            raise _reject(
                "conflict_repair_required", "repair_required",
                "candidate touches paths the integration branch changed since the merge base",
                retry_requires_new_evidence=True, conflicted_paths=overlap[:50],
                integration_head=old_head,
            )
    worktree.parent.mkdir(parents=True, exist_ok=True)
    git.guard_drivers()
    added = git.run("worktree", "add", "--quiet", "--detach", str(worktree), old_head)
    if added.returncode != 0:
        raise IntegrationRefused(
            "cannot create the detached integration worktree",
            reason_code="integration_worktree_failed", error=added.stderr.strip()[-300:],
        )
    identity = {
        "GIT_AUTHOR_NAME": "agentctl integrator",
        "GIT_AUTHOR_EMAIL": "integrator@agentctl.invalid",
        "GIT_COMMITTER_NAME": "agentctl integrator",
        "GIT_COMMITTER_EMAIL": "integrator@agentctl.invalid",
        "GIT_AUTHOR_DATE": f"{planned_at} +0000",
        "GIT_COMMITTER_DATE": f"{planned_at} +0000",
    }
    git.guard_drivers(worktree)
    if old_head == evidence.base_commit:
        strategy = "fast_forward"
        merged = git.run("merge", "--ff-only", "--quiet", candidate, cwd=worktree, env=identity)
    else:
        strategy = "merge_no_ff"
        merged = git.run(
            "merge", "--no-ff", "--no-edit", "--quiet", "-m",
            f"agentctl: integrate {evidence.task_id} ({integration_id}) into {branch}",
            candidate, cwd=worktree, env=identity,
        )
    if merged.returncode != 0:
        conflicts = (git.out("diff", "--name-only", "--no-ext-diff", "--diff-filter=U", cwd=worktree) or "").split()
        git.run("merge", "--abort", cwd=worktree)
        if conflicts:
            raise _reject(
                "conflict_repair_required", "repair_required",
                "candidate conflicts with the integration branch",
                retry_requires_new_evidence=True, conflicted_paths=conflicts[:50],
                integration_head=old_head,
            )
        raise _reject(
            "merge_failed", "human_required", "git could not merge the candidate",
            error=(merged.stderr or merged.stdout).strip()[-300:],
        )
    planned = git.out("rev-parse", "HEAD", cwd=worktree)
    tree = git.out("rev-parse", "HEAD^{tree}", cwd=worktree)
    parents = (git.out("rev-list", "--parents", "-n1", "HEAD", cwd=worktree) or "").split()[1:]
    if planned is None or tree is None:
        raise IntegrationRefused("cannot read the planned commit", reason_code="plan_unreadable")
    preserved = (
        planned == candidate if strategy == "fast_forward" else parents == [old_head, candidate]
    ) and git.diff_hash(evidence.base_commit, candidate) == evidence.diff_hash
    if preserved and strategy == "merge_no_ff":
        # Every path the candidate touched must carry exactly the candidate's content.
        preserved = not (touched & git.changed_paths(candidate, planned))
    if not preserved:
        raise _reject(
            "candidate_altered", "human_required",
            "the planned result does not carry the reviewed candidate unchanged",
        )
    if isolation == ISOLATION_LEGACY:
        # LEGACY (explicit AGENTCTL_CHECK_ISOLATION=legacy-unsafe): unsandboxed gates on a pristine copy of
        # the planned tree with no ``.git`` link, outside the checkout, with the before/after tamper check.
        own_metadata = git.out("rev-parse", "--absolute-git-dir", cwd=worktree)
        exclude = Path(own_metadata).resolve() if own_metadata else None
        with tempfile.TemporaryDirectory(prefix="agentctl-integration-gate-") as gate_root:
            gate_tree = Path(gate_root) / "tree"
            _materialize_tree(git, planned, gate_tree)
            before = _protected_state(git, branch, exclude)
            gates = [
                {"gate": "integration:candidate-commit-preserved", "result": "pass"},
                *run_gate_commands(evidence.checks, cwd=gate_tree, timeout=check_timeout),
            ]
            after = _protected_state(git, branch, exclude)
        environment = legacy_environment("none", timeout=check_timeout)
        changed = _changed(before, after)
        if changed:
            # Nothing is restored: the changed metadata stays for a human to inspect, and
            # integration is refused while the repository is still in this state.
            raise _reject(
                "gate_tampered_repository", "human_required",
                "a gate changed repository metadata the integrator relies on",
                retry_requires_new_evidence=True, integration_head=old_head,
                changed=changed[:50], tampered_state=sha256_of_json(after), gates=gates,
            )
    else:
        # The one check-environment contract, identical to verification: the planned commit's tree in the
        # runc sandbox with a private synthetic repository. The repository is not reachable from a check,
        # so no before/after tamper comparison is needed.
        try:
            outcome = run_candidate_checks(
                git.root, planned, evidence.checks, label="integrate", timeout=check_timeout,
                spec_fields={"task_id": evidence.task_id, "tree_hash": tree},
            )
        except CandidateTreeUnsafe as exc:
            raise _unsafe(exc.path, exc.why) from exc
        except CandidateTreeUnreadable as exc:
            raise IntegrationRefused("cannot read the planned tree", **exc.context) from exc
        except CheckEnvironmentUnavailable as exc:
            raise _refusal(exc) from exc
        environment = outcome["environment"]
        if outcome.get("failure_reason"):
            raise _reject(
                str(outcome["failure_reason"]), "human_required",
                "the check environment could not be established for the planned tree",
                retry_requires_new_evidence=True, integration_head=old_head, environment=environment,
            )
        gates = [{"gate": "integration:candidate-commit-preserved", "result": "pass"}, *outcome["checks"]]
    if any(gate["result"] != "pass" for gate in gates):
        raise _reject(
            "affected_check_failed", "repair_required",
            "an affected gate failed on the resulting integration tree",
            retry_requires_new_evidence=True, integration_head=old_head,
            planned_commit=planned, gates=gates, environment=environment,
        )
    return _Plan(strategy, planned, tree, gates, True, environment)


# ------------------------------------------------------------- manifest ----


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _manifest(intent: Mapping[str, Any], lease: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "artifact_type": "integration-manifest",
        "integration_id": intent["integration_id"],
        "task_id": intent["task_id"],
        "integrator_role": "integrator",
        "integrator_session_id": lease.get("integrator_run_id", intent["integrator_run_id"]),
        "lease_id": lease["lease_id"],
        "lease_epoch": lease["lease_epoch"],
        "fencing_token": lease["fencing_token"],
        "candidate_result_commit": intent["candidate_commit"],
        "reviewed_diff_hash": intent["reviewed_diff_hash"],
        "review_id": intent["review_id"],
        "integration_base_commit": intent["expected_old_head"],
        "integration_commit": intent["planned_commit"],
        "merge_strategy": "conflict_free_preserving_candidate",
        "candidate_patch_unchanged": intent["candidate_patch_unchanged"] is True,
        "dependency_interface_hashes": [
            {"interface": name, "hash": digest} for name, digest in intent["interface_hashes"]
        ],
        "affected_gates_rerun": [
            {"gate": gate["gate"], "result": gate["result"]} for gate in intent["gates"]
        ],
        "completed_at": _now(),
    }


# ---------------------------------------------------------------- leases ----


def _proc_start_ticks(pid: int) -> int | None:
    """Kernel start time of ``pid`` in clock ticks (``/proc/<pid>/stat`` field 22), or None."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            rest = handle.read().rsplit(b")", 1)[1].split()
        return None if rest[0] == b"Z" else int(rest[19])
    except (OSError, IndexError, ValueError):
        return None


def _boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def _holder_identity() -> dict[str, Any]:
    """Who holds a lease: this process, identified so a later process can tell whether it still lives."""
    return {"pid": os.getpid(), "start_ticks": _proc_start_ticks(os.getpid()), "boot_id": _boot_id()}


def _holder_status(holder: object) -> str:
    """``alive`` (another live process), ``dead`` or ``unknown`` for the holder recorded in a lease.

    The process lock makes a concurrent live integrator impossible through ``integrate()``; this is the
    check that a lease is never adopted from a holder that is demonstrably still running anyway. A holder
    that is this very process is a previous, finished call (the lock is held now), not a concurrent one.
    """
    if not isinstance(holder, Mapping) or not isinstance(holder.get("pid"), int):
        return "unknown"
    if holder.get("boot_id") and _boot_id() and holder["boot_id"] != _boot_id():
        return "dead"  # a different boot: that process cannot exist
    if holder["pid"] == os.getpid():
        return "dead"
    ticks = _proc_start_ticks(holder["pid"])
    if ticks is None:
        return "dead"
    return "alive" if holder.get("start_ticks") in (None, ticks) else "dead"  # pid reuse is "dead"


def _renew_expired_lease(
    store: EventStore, *, intent: Mapping[str, Any], expired: Mapping[str, Any], lease: Mapping[str, Any]
) -> dict[str, Any]:
    """Replace ``expired`` (the lease this process holds) with ``lease``: new epoch and fencing token.

    Inside the same transaction that records the renewal this verifies that ``expired`` is still the
    current integrable lease (nobody took over meanwhile: ``lease_fencing_mismatch``) AND that it is
    expired as stored (``lease_not_expired``): a live lease can never be replaced. The old lease artifact
    is retired (``integrable = 0``) so its token can never complete the integration.
    """
    task_id = str(intent["task_id"])
    artifact_id = f"integration-lease:{task_id}:{lease['lease_id']}"
    event = make_event(
        event_type="integration_lease_renewed",
        reason_code="integration_lease_expired",
        actor_role="integrator",
        task_id=task_id,
        attempt_id=str(intent["attempt_id"]),
        prior_state=TaskState.INTEGRATING.value,
        next_state=TaskState.INTEGRATING.value,
        artifact_refs=[artifact_id, f"intent:{intent['integration_id']}"],
    )

    def mutation(connection: Any) -> dict[str, Any]:
        intent_row = connection.execute(
            "SELECT integrable FROM artifacts WHERE artifact_id = ?",
            (f"integration-intent:{intent['integration_id']}",),
        ).fetchone()
        state = connection.execute("SELECT state FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if (
            intent_row is None or intent_row["integrable"] != 1
            or state is None or state["state"] != TaskState.INTEGRATING.value
        ):
            raise IntegrationRefused(
                "lease can be renewed only for an integrating task with a live intent",
                reason_code="reconciliation_required", task_id=task_id,
            )
        row = connection.execute(
            "SELECT artifact_id, payload_json FROM artifacts WHERE artifact_type = 'integration-lease' "
            "AND task_id = ? AND integrable = 1 ORDER BY rowid DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        current = json.loads(row["payload_json"]) if row else {}
        if (
            row is None
            or row["artifact_id"] != f"integration-lease:{task_id}:{expired['lease_id']}"
            or current.get("lease_epoch") != expired["lease_epoch"]
            or current.get("fencing_token") != expired["fencing_token"]
        ):
            raise IntegrationRefused(
                "the lease being renewed is not the current integration lease; another holder took over",
                reason_code="lease_fencing_mismatch", task_id=task_id,
            )
        if lease_is_live(current):
            raise IntegrationRefused(
                "the integration lease has not expired; it cannot be replaced",
                reason_code="lease_not_expired", task_id=task_id, expires_at=current.get("expires_at"),
            )
        connection.execute(
            "UPDATE artifacts SET integrable = 0 WHERE task_id = ? AND artifact_type = 'integration-lease'",
            (task_id,),
        )
        connection.execute(
            "INSERT INTO artifacts(artifact_id, artifact_type, task_id, attempt_id, "
            "content_hash, path, integrable, payload_json) VALUES "
            "(?, 'integration-lease', ?, ?, ?, '', 1, ?)",
            (
                artifact_id, task_id, intent["attempt_id"], lease["fencing_token"],
                json.dumps(dict(lease), sort_keys=True, separators=(",", ":")),
            ),
        )
        return {"task_id": task_id, "lease_epoch": lease["lease_epoch"]}

    return dict(store.execute(f"integration-lease-renew:{lease['lease_id']}", event, mutation))


# --------------------------------------------------------------- engine ----


class _Engine:
    def __init__(
        self,
        store: EventStore,
        settings: IntegrationSettings,
        integrator_id: str,
        fault_hook: FaultHook | None,
    ) -> None:
        self.store = store
        self.root = store.repository_root
        self.settings = settings
        self.integrator_id = integrator_id
        self.git = _Git(self.root)
        self.fault = fault_hook or (lambda _point: None)
        self.query = store_query(store)

    # -- records ------------------------------------------------------------

    def _current_lease(self, task_id: str) -> dict[str, Any]:
        lease = current_lease(self.query, task_id)
        if lease is None:
            raise IntegrationRefused(
                "no integrable integration lease is on record",
                reason_code="reconciliation_required", task_id=task_id,
            )
        return lease

    def _mint_lease(self, task_id: str, integration_id: str, epoch: int) -> dict[str, Any]:
        lease = new_lease(task_id, integration_id, epoch, self.integrator_id)
        lease["holder"] = _holder_identity()
        return lease

    def _held_lease(self, task_id: str) -> dict[str, Any]:
        """The lease a reconciling process may act under: its own, or one whose holder is demonstrably gone.

        A live (unexpired) lease whose holder is another LIVE process is never adopted
        (``lease_held_by_live_holder``); a live lease with no identifiable holder is not adopted either
        (``lease_holder_unknown``: wait for it to expire). An expired lease is returned as is: the caller
        replaces it through the checked renewal, which re-verifies expiry inside its transaction.
        """
        lease = self._current_lease(task_id)
        if lease.get("integrator_run_id") == self.integrator_id or not lease_is_live(lease):
            return lease
        status = _holder_status(lease.get("holder"))
        if status == "alive":
            raise IntegrationRefused(
                "the integration lease is held by another live process; it is not adopted",
                reason_code="lease_held_by_live_holder", task_id=task_id,
                holder_pid=(lease.get("holder") or {}).get("pid"),
            )
        if status == "unknown":
            raise IntegrationRefused(
                "the integration lease is live and its holder cannot be identified; wait for it to expire",
                reason_code="lease_holder_unknown", task_id=task_id, expires_at=lease.get("expires_at"),
            )
        return lease  # its holder is gone: this process takes over under the process lock

    def _renewed_if_expired(self, intent: Mapping[str, Any], lease: Mapping[str, Any]) -> Mapping[str, Any]:
        """``lease`` itself while live; otherwise its replacement (new epoch and token), renewed from ``lease``."""
        if lease_is_live(lease):
            return lease
        task_id = str(intent["task_id"])
        replacement = self._mint_lease(
            task_id, str(intent["integration_id"]), next_lease_epoch(self.query, task_id)
        )
        _renew_expired_lease(self.store, intent=intent, expired=lease, lease=replacement)
        return replacement

    def _finalize(
        self, intent: Mapping[str, Any], evidence: _Evidence, lease: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Write and validate the manifest on disk, then run transaction 2 under ``lease``.

        ``lease`` is the lease THIS process claimed (or took over from a demonstrably dead holder), never
        whatever the store currently shows: if it is expired it is renewed from itself, and transaction 2
        then compares the manifest with the store's current lease, so a different current lease (another
        holder took over) is refused as ``lease_fencing_mismatch`` rather than silently adopted.
        """
        lease = self._renewed_if_expired(intent, lease)
        manifest = _manifest(intent, lease)
        directory = (
            _integration_dir(self.root, str(intent["task_id"]))
            / "records" / str(intent["integration_id"])
        )
        directory.mkdir(parents=True, exist_ok=True)
        for name, document in (
            ("integration-manifest.json", manifest),
            ("review-verdict.json", dict(evidence.review_payload)),
            ("implementation-manifest.json", dict(evidence.implementation_manifest)),
        ):
            (directory / name).write_text(
                json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8"
            )
        validate_artifact(directory / "integration-manifest.json", repository_root=self.root)
        complete_integration(self.store, intent=intent, manifest=manifest)
        return manifest

    @staticmethod
    def _result(outcome: str, manifest: Mapping[str, Any], intent: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "outcome": outcome,
            "task_id": manifest["task_id"],
            "integration_id": manifest["integration_id"],
            "integration_commit": manifest["integration_commit"],
            "integration_base_commit": manifest["integration_base_commit"],
            "candidate_result_commit": manifest["candidate_result_commit"],
            "branch": intent.get("branch"),
            "strategy": intent.get("strategy"),
            "integrator_run_id": manifest["integrator_session_id"],
            "affected_gates": manifest["affected_gates_rerun"],
        }

    # -- reconciliation -----------------------------------------------------

    def reconcile(self) -> list[dict[str, Any]]:
        """Settle every task found in ``integrating`` from actual Git state."""
        settled: list[dict[str, Any]] = []
        for row in self.store.rows(
            "SELECT task_id FROM tasks WHERE state = 'integrating' ORDER BY task_id"
        ):
            task_id = row["task_id"]
            intent = latest_intent(self.query, task_id)
            if intent is None:
                raise IntegrationRefused(
                    "task is integrating but has no intent artifact",
                    reason_code="reconciliation_required", task_id=task_id,
                )
            head = self.git.branch_head(str(intent["branch"]))
            planned = str(intent["planned_commit"])
            applied = head is not None and (
                head == planned or planned in self.git.first_parent_chain(head)
            )
            if applied:
                lease = self._held_lease(task_id)  # refuses before anything is written
                record_reconciliation(
                    self.store, intent=intent, finding="git_step_applied", branch_head=head,
                    actions="completing the event-store side without redoing Git",
                )
                evidence = self._recovery_evidence(intent)
                manifest = self._finalize(intent, evidence, lease)
                settled.append(self._result("reconciled", manifest, intent))
            elif head == intent["branch_head_before"]:
                self._held_lease(task_id)  # refuses before anything is written
                record_reconciliation(
                    self.store, intent=intent, finding="git_step_not_applied", branch_head=head,
                    actions="retrying the Git step under the same intent",
                )
                settled.append(self._resume(intent))
            else:
                record_reconciliation(
                    self.store, intent=intent, finding="branch_moved_unrelated", branch_head=head,
                    actions="refused; human inspection required",
                )
                raise IntegrationRefused(
                    "integration branch moved to a commit unrelated to the recorded intent",
                    reason_code="reconciliation_required", task_id=task_id,
                    branch_head=head, expected_old_head=intent["expected_old_head"],
                    planned_commit=planned,
                )
        return settled

    def _recovery_evidence(self, intent: Mapping[str, Any]) -> _Evidence:
        """Re-run full verification while recovering; failure leaves the task for a human."""
        try:
            evidence = _verify(
                self.store, self.git, str(intent["task_id"]), self.integrator_id
            )
        except IntegrationRejected as exc:
            raise IntegrationRefused(
                "evidence no longer verifies; the task stays integrating for a human",
                reason_code="reconciliation_required", task_id=intent["task_id"],
                verification_failure=exc.reason_code,
            ) from exc
        if (
            evidence.result_commit != intent["candidate_commit"]
            or evidence.diff_hash != intent["reviewed_diff_hash"]
            or evidence.review_id != intent["review_id"]
        ):
            raise IntegrationRefused(
                "candidate evidence changed since the intent was recorded",
                reason_code="reconciliation_required", task_id=intent["task_id"],
            )
        return evidence

    def _resume(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        """Retry a Git step that never happened, under the same intent and lease."""
        task_id = str(intent["task_id"])
        evidence = self._recovery_evidence(intent)
        worktree = _integration_dir(self.root, task_id) / f"wt-{intent['integration_id']}"
        try:
            plan = _plan(
                self.git, evidence, old_head=str(intent["expected_old_head"]),
                planned_at=int(intent["planned_at"]), integration_id=str(intent["integration_id"]),
                branch=str(intent["branch"]), worktree=worktree,
                check_timeout=self.settings.check_timeout,
            )
        except IntegrationRejected as exc:
            raise IntegrationRefused(
                "recorded intent cannot be replayed; the task stays integrating for a human",
                reason_code="reconciliation_required", task_id=task_id,
                replay_failure=exc.reason_code,
            ) from exc
        finally:
            self.git.remove_worktree(worktree)
        if plan.commit != intent["planned_commit"]:
            raise IntegrationRefused(
                "replayed plan differs from the recorded intent",
                reason_code="reconciliation_required", task_id=task_id,
                recorded=intent["planned_commit"], replayed=plan.commit,
            )
        lease = self._renewed_if_expired(intent, self._held_lease(task_id))
        manifest = self._advance_and_complete(intent, evidence, lease)  # _finalize re-checks the lease
        return self._result("reconciled", manifest, intent)

    def _advance_and_complete(
        self, intent: Mapping[str, Any], evidence: _Evidence, lease: Mapping[str, Any]
    ) -> dict[str, Any]:
        branch = str(intent["branch"])
        before = intent["branch_head_before"]
        moved = self.git.compare_and_swap(
            branch, str(intent["planned_commit"]), before,
            f"agentctl integrate {intent['task_id']} {intent['integration_id']}",
        )
        if not moved and self.git.branch_head(branch) != intent["planned_commit"]:
            raise IntegrationRefused(
                "integration branch moved during integration; compare-and-swap refused",
                reason_code="branch_moved_during_integration", task_id=intent["task_id"],
                expected=before, actual=self.git.branch_head(branch),
            )
        self.fault("after_ref_update")
        return self._finalize(intent, evidence, lease)

    # -- selection ----------------------------------------------------------

    def existing_record(self, task_id: str) -> dict[str, Any] | None:
        rows = self.store.rows(
            "SELECT payload_json FROM artifacts WHERE artifact_id = ?",
            (f"integration-record:{task_id}",),
        )
        return json.loads(rows[0]["payload_json"]) if rows else None

    def refuse_if_still_tampered(self) -> None:
        """Refuse while the repository is still in a state a gate was caught producing."""
        rows = self.store.rows(
            "SELECT payload_json FROM artifacts WHERE artifact_type = 'integration-rejection' "
            "AND json_extract(payload_json, '$.reason_code') = 'gate_tampered_repository' "
            "ORDER BY rowid DESC LIMIT 1"
        )
        if not rows:
            return
        details = json.loads(rows[0]["payload_json"]).get("details") or {}
        current = sha256_of_json(_protected_state(self.git, self.settings.branch))
        if details.get("tampered_state") == current:
            raise IntegrationRefused(
                "repository metadata is still as a gate left it; a human must inspect it",
                reason_code="gate_tampered_repository", changed=details.get("changed"),
            )

    def run(self, task_id: str | None) -> dict[str, Any]:
        self.git.validate_branch(self.settings.branch)
        self.refuse_if_still_tampered()
        settled = self.reconcile()
        head = self.git.branch_head(self.settings.branch)
        queue = build_queue(self.query, head)
        reconciled = [s["task_id"] for s in settled]

        if task_id is None:
            entry = queue.head
            if entry is None:
                if settled:
                    return {**settled[-1], "reconciled": reconciled}
                return {
                    "outcome": "queue_empty",
                    "reason_code": "queue_empty",
                    "waiting_on_dependencies": {
                        e.task_id: list(e.waiting_on) for e in queue.entries if e.waiting_on
                    },
                    "parked_without_new_evidence": {
                        e.task_id: e.parked_reason for e in queue.entries if e.parked_reason
                    },
                    # The remedy, not only the code, for the parked reasons that have one.
                    "parked_explanations": {
                        e.task_id: e.parked_explanation for e in queue.entries if e.parked_explanation
                    },
                }
            target = entry.task_id
        else:
            target = task_id
            earlier = self._preflight_explicit(target, queue)
            if earlier is not None:
                earlier["reconciled"] = reconciled
                return earlier
        result = self._integrate_candidate(target)
        if settled:
            result["reconciled"] = reconciled
        return result

    def _preflight_explicit(self, task_id: str, queue: IntegrationQueue) -> dict[str, Any] | None:
        if not _TASK_ID.match(task_id):
            raise IntegrationRefused("malformed task id", reason_code="task_unknown", task_id=task_id)
        row = self.store.rows("SELECT state FROM tasks WHERE task_id = ?", (task_id,))
        if not row:
            raise IntegrationRefused("unknown task", reason_code="task_unknown", task_id=task_id)
        state = row[0]["state"]
        if state == TaskState.COMPLETED.value:
            record = self.existing_record(task_id)
            if record is None:
                raise IntegrationRefused(
                    "task is completed but has no integration record",
                    reason_code="record_missing", task_id=task_id,
                )
            head = self.git.branch_head(self.settings.branch)
            contained = head is not None and self.git.is_ancestor(record["integration_commit"], head)
            return {
                "outcome": "already_integrated" if contained else "record_git_divergence",
                "reason_code": None if contained else "record_git_divergence",
                "task_id": task_id,
                "integration_id": record["integration_id"],
                "integration_commit": record["integration_commit"],
                "record": record,
                "git_contains_record": contained,
            }
        if state != TaskState.INTEGRATION_READY.value:
            raise IntegrationRefused(
                "task is not integration_ready", reason_code="task_not_integration_ready",
                task_id=task_id, state=state,
            )
        entry = queue.entry(task_id)
        if entry is not None and entry.parked_reason == "dependency_canceled":
            raise IntegrationRefused(
                "a required dependency was canceled; this task cannot be integrated until it is "
                "replanned or superseded (moving the integration branch will not change that)",
                reason_code="retry_without_new_evidence", task_id=task_id,
                prior_reason=entry.parked_reason,
            )
        if entry is not None and entry.parked_reason is not None:
            raise IntegrationRefused(
                "retry without new evidence is forbidden; the integration branch has not "
                "moved since the last recorded rejection",
                reason_code="retry_without_new_evidence", task_id=task_id,
                prior_reason=entry.parked_reason,
            )
        if entry is not None and entry.waiting_on:
            return None  # verification records the dependency rejection
        head = queue.head
        if head is None or head.task_id != task_id:
            raise IntegrationRefused(
                "task is not the integration queue head", reason_code="not_queue_head",
                task_id=task_id, head=None if head is None else head.task_id,
            )
        return None

    # -- one candidate ------------------------------------------------------

    def _integrate_candidate(self, task_id: str) -> dict[str, Any]:
        state = self.store.status(task_id)["state"]
        attempts = self.store.rows(
            "SELECT attempt_id FROM attempts WHERE task_id = ? ORDER BY attempt_number DESC LIMIT 1",
            (task_id,),
        )
        attempt = attempts[0]["attempt_id"] if attempts else None
        head = self.git.branch_head(self.settings.branch)
        try:
            evidence = _verify(self.store, self.git, task_id, self.integrator_id)
            return self._plan_claim_advance(evidence, head)
        except IntegrationRejected as exc:
            recorded = record_rejection(
                self.store, task_id=task_id, attempt_id=attempt, state=state, rejection=exc,
                integration_head=head, integrator_id=self.integrator_id,
            )
            return {
                "outcome": "rejected",
                "task_id": task_id,
                "reason_code": exc.reason_code,
                "rejection_outcome": exc.outcome,
                "message": exc.message,
                "details": exc.details,
                "task_state": recorded["state"],
                "integration_branch_head": head,
                "rejection_artifact": recorded["artifact_id"],
            }

    def _plan_claim_advance(self, evidence: _Evidence, head: str | None) -> dict[str, Any]:
        task_id = evidence.task_id
        branch = self.settings.branch
        base = evidence.base_commit
        if head is None and self.settings.base is not None:
            resolved = self.git.out("rev-parse", "--verify", "-q", f"{self.settings.base}^{{commit}}")
            if resolved is None:
                raise IntegrationRefused(
                    "configured integration base does not exist",
                    reason_code="integration_branch_invalid", base=self.settings.base,
                )
            base = resolved
        old_head = head or base
        epoch = next_lease_epoch(self.query, task_id)
        integration_id = f"{task_id}-I{epoch}"
        planned_at = int(time.time())
        worktree = _integration_dir(self.root, task_id) / f"wt-{integration_id}"
        try:
            plan = _plan(
                self.git, evidence, old_head=old_head, planned_at=planned_at,
                integration_id=integration_id, branch=branch, worktree=worktree,
                check_timeout=self.settings.check_timeout,
            )
            self.fault("after_plan")
            lease = self._mint_lease(task_id, integration_id, epoch)
            intent = {
                "task_id": task_id,
                "attempt_id": evidence.attempt_id,
                "integration_id": integration_id,
                "branch": branch,
                "branch_head_before": head,
                "expected_old_head": old_head,
                "candidate_commit": evidence.result_commit,
                "candidate_base_commit": evidence.base_commit,
                "candidate_tree_hash": evidence.tree_hash,
                "reviewed_diff_hash": evidence.diff_hash,
                "candidate_patch_unchanged": plan.patch_unchanged,
                "review_id": evidence.review_id,
                "strategy": plan.strategy,
                "planned_commit": plan.commit,
                "planned_tree": plan.tree,
                "planned_at": planned_at,
                "integrator_run_id": self.integrator_id,
                "integrator_role": "integrator",
                "lease_id": lease["lease_id"],
                "lease_epoch": epoch,
                "interface_hashes": [list(pair) for pair in evidence.interface_hashes],
                "gates": plan.gates,
                "check_environment": plan.environment,
            }
            claim_integration(
                self.store, intent=intent, lease=lease,
                branch_head=lambda: self.git.branch_head(branch),
            )
        finally:
            self.git.remove_worktree(worktree)
        self.fault("after_intent")
        if not lease_is_live(lease):
            raise IntegrationRefused(
                "integration lease expired before the Git step", reason_code="lease_expired",
                task_id=task_id,
            )
        manifest = self._advance_and_complete(intent, evidence, lease)
        return self._result("integrated", manifest, intent)


# ----------------------------------------------------------- entrypoint ----


@contextlib.contextmanager
def _process_lock(root: Path, timeout: float) -> Iterator[None]:
    """Process-level mutex over Git-side effects.

    This only prevents a live integration being "reconciled" by a second process.
    The authoritative single-slot guarantee is the claim transaction.
    """
    path = root / "agent-system" / "runtime" / "integration.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise IntegrationBusy(
                        "timed out waiting for another integration process",
                        reason_code="integration_in_progress",
                    )
                time.sleep(0.05)
        yield
    finally:
        handle.close()


def _clean_leftover_worktrees(git: _Git, root: Path) -> None:
    """Remove detached worktrees left by a crashed run (the lock makes this safe)."""
    base = root / "agent-system" / "runtime" / "integration"
    if base.is_dir():
        for leftover in base.glob("*/wt-*"):
            git.remove_worktree(leftover)
    git.run("worktree", "prune")


def integrate(
    task_id: str | None = None,
    *,
    store: EventStore | None = None,
    settings: IntegrationSettings | None = None,
    fault_hook: FaultHook | None = None,
) -> dict[str, Any]:
    """Integrate the queue head (or the explicitly named head) and return the outcome.

    ``fault_hook`` exists only so tests can kill the process at a named point
    (``after_plan``, ``after_intent``, ``after_ref_update``); production callers
    never pass it.
    """
    store = store or EventStore()
    settings = settings or IntegrationSettings()
    integrator_id = settings.integrator_id or f"integrator-run:{uuid.uuid4()}"
    engine = _Engine(store, settings, integrator_id, fault_hook)
    try:
        with _process_lock(store.repository_root, settings.lock_timeout):
            _clean_leftover_worktrees(engine.git, store.repository_root)
            result = engine.run(task_id)
    except IntegrationRefused as exc:
        return {
            "outcome": "refused",
            "reason_code": exc.reason_code,
            "message": exc.message,
            "details": {k: v for k, v in exc.context.items() if k != "reason_code"},
        }
    result.setdefault("integrator_run_id", integrator_id)
    return result


def integrate_task(task_id: str | None = None, **kwargs: Any) -> dict[str, Any]:
    """Backward-compatible name for :func:`integrate`."""
    return integrate(task_id, **kwargs)
