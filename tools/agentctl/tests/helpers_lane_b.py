"""Lane B fixtures: real disposable Git repositories and real ``integration_ready`` tasks.

Candidates are produced exclusively through the existing public API --
``Scheduler.register_task/dispatch/acknowledge/ingest_manifest/verify_candidate``
and ``ReviewManager.set_author_identity/assign/ingest`` -- with a real commit made
in a real leased worktree. Nothing here writes the ``integration_ready`` state
directly; the few deliberate corruptions go through :meth:`Lab.tamper` and every
test that uses it labels the result simulated.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from typing import Any

from agentctl.events import make_event
from agentctl.review import ReviewIdentity, ReviewManager
from agentctl.scheduler import Scheduler, TaskSpec
from agentctl.store import EventStore

AGENTCTL_DIR = pathlib.Path(__file__).resolve().parents[1]
SOURCE_REPO = pathlib.Path(
    subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], cwd=pathlib.Path(__file__).parent, text=True
    ).strip()
)
INTERFACE_ID = "dry-run:context-v1"
INTERFACE_SOURCE = "agent-system/work/DRY-RUN/interfaces/context-v1.json"
BRANCH = "agentctl/integration"

_IDENTITY = {
    "GIT_AUTHOR_NAME": "Candidate Author",
    "GIT_AUTHOR_EMAIL": "author@example.invalid",
    "GIT_COMMITTER_NAME": "Candidate Author",
    "GIT_COMMITTER_EMAIL": "author@example.invalid",
}


@dataclass
class Candidate:
    task_id: str
    attempt_id: str
    result_commit: str
    base_commit: str
    worktree: pathlib.Path
    files: dict[str, str]
    author_session: str
    reviewer_session: str | None
    spec: TaskSpec
    verdict: dict[str, Any] = field(default_factory=dict)


@dataclass
class CliResult:
    code: int
    stdout: str
    stderr: str

    @property
    def json(self) -> dict[str, Any]:
        return json.loads(self.stdout.strip().splitlines()[-1])


class Lab:
    """One disposable repository, event store, scheduler and review manager."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name) / "repository"
        subprocess.run(
            ["git", "clone", "--quiet", "--shared", str(SOURCE_REPO), str(self.root)],
            check=True,
        )
        self.base = self.git("rev-parse", "HEAD")
        self.checkout_branch = self.git("symbolic-ref", "--short", "HEAD")
        database = self.root / "agent-system" / "runtime" / "agentctl.sqlite3"
        self.store = EventStore(database, repository_root=self.root)
        self.scheduler = Scheduler(self.store)
        self.reviews = ReviewManager(self.store)

    def close(self) -> None:
        self._tmp.cleanup()

    # -- git ----------------------------------------------------------------

    def git(self, *args: str, cwd: pathlib.Path | None = None, check: bool = True) -> str:
        env = {**os.environ, **_IDENTITY}
        done = subprocess.run(
            ["git", *args], cwd=cwd or self.root, env=env, capture_output=True, text=True
        )
        if check and done.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {done.stderr}")
        return done.stdout.strip()

    def branch_head(self, branch: str = BRANCH) -> str | None:
        done = subprocess.run(
            ["git", "rev-parse", "--verify", "-q", f"refs/heads/{branch}"],
            cwd=self.root, capture_output=True, text=True,
        )
        return done.stdout.strip() if done.returncode == 0 else None

    def tree_file(self, path: str, branch: str = BRANCH) -> str | None:
        done = subprocess.run(
            ["git", "show", f"refs/heads/{branch}:{path}"],
            cwd=self.root, capture_output=True, text=True,
        )
        return done.stdout if done.returncode == 0 else None

    def commit_count(self, branch: str = BRANCH) -> int:
        return int(self.git("rev-list", "--count", f"refs/heads/{branch}"))

    def leftover_integration_worktrees(self) -> list[str]:
        base = self.root / "agent-system" / "runtime" / "integration"
        return sorted(str(p) for p in base.glob("*/wt-*")) if base.is_dir() else []

    # -- event store --------------------------------------------------------

    def state(self, task_id: str) -> str:
        return str(self.store.status(task_id)["state"])

    def events(self, event_type: str | None = None, task_id: str | None = None) -> list[Any]:
        sql, params = "SELECT * FROM events", []
        clauses = []
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return self.store.rows(sql + " ORDER BY sequence", params)

    def artifacts(self, artifact_type: str, task_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM artifacts WHERE artifact_type = ?"
        params: list[Any] = [artifact_type]
        if task_id:
            sql += " AND task_id = ?"
            params.append(task_id)
        return [
            {**dict(row), "payload": json.loads(row["payload_json"])}
            for row in self.store.rows(sql + " ORDER BY rowid", params)
        ]

    def tamper(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        """SIMULATED corruption: mutate stored state outside the public API.

        Used only to test that integration re-derives evidence instead of
        trusting stored flags. Each caller labels its result as simulated.
        """
        event = make_event(
            event_type="test_fixture_mutation",
            reason_code="simulated_fixture",
            actor_role="test-harness",
        )
        self.store.execute(f"fixture:{uuid.uuid4()}", event, lambda c: c.execute(sql, params).rowcount)

    # -- candidates ---------------------------------------------------------

    def candidate(
        self,
        task_id: str,
        files: dict[str, str],
        *,
        checks: tuple[str, ...] = (),
        dependencies: tuple[str, ...] = (),
        interface_pins: dict[str, str] | None = None,
        review: bool = True,
        author_session: str | None = None,
        reviewer_session: str | None = None,
    ) -> Candidate:
        author_session = author_session or f"author-session-{task_id}"
        reviewer_session = reviewer_session or f"reviewer-session-{task_id}"
        digest = hashlib.sha256(task_id.encode()).hexdigest()
        spec = TaskSpec(
            task_id=task_id,
            work_package_id="WP-LANE-B",
            contract_hash=digest,
            context_hash=hashlib.sha256(digest.encode()).hexdigest(),
            base_commit=self.base,
            capability_class="standard_implementation",
            risk="low",
            enforcement_class="sandboxed_tools",
            writable_paths=tuple(files),
            prohibited_paths=("AppleBooks/**",),
            semantic_locks={},
            maximum_attempts=2,
            maximum_artifact_corrections=1,
            dependencies=dependencies,
            interface_pins=interface_pins,
            deterministic_checks=checks,
        )
        self.scheduler.register_task(spec)
        lease = self.scheduler.dispatch(task_id, worker_identity=author_session)
        self.scheduler.acknowledge(task_id, spec.context_hash)
        worktree = self.root / lease.worktree_path
        worktree.parent.mkdir(parents=True, exist_ok=True)
        self.git("worktree", "add", "--quiet", "--detach", str(worktree), self.base)
        changed = []
        for path, content in files.items():
            target = worktree / path
            target.parent.mkdir(parents=True, exist_ok=True)
            existed = subprocess.run(
                ["git", "cat-file", "-e", f"{self.base}:{path}"], cwd=self.root,
                capture_output=True,
            ).returncode == 0
            target.write_text(content, encoding="utf-8")
            changed.append({"path": path, "change_type": "modified" if existed else "added"})
        self.git("add", "--", *files, cwd=worktree)
        self.git("commit", "--quiet", "-m", f"{task_id}: candidate", cwd=worktree)
        result = self.git("rev-parse", "HEAD", cwd=worktree)
        self.scheduler.ingest_manifest(
            task_id,
            {
                "artifact_type": "implementation-manifest",
                "task_id": task_id,
                "attempt_id": lease.attempt_id,
                "lease_id": lease.lease_id,
                "lease_epoch": lease.epoch,
                "fencing_token": lease.fencing_token,
                "worker_role": spec.capability_class,
                "provider": "fixture-provider",
                "model": "fixture-model",
                "model_version": "fixture-version",
                "contract_hash": spec.contract_hash,
                "context_hash": spec.context_hash,
                "base_commit": spec.base_commit,
                "result_commit": result,
                "files_changed": changed,
                "commands_executed": [],
                "acceptance_criteria_addressed": ["synthetic"],
                "assumptions": [],
                "decisions": [],
                "deviations": [],
                "discovered_dependencies": [],
                "unresolved_risks": [],
                "usage": {"input_tokens": 0, "output_tokens": 0, "latency_ms": 0, "cost_usd": 0},
                "termination_reason": "completed",
            },
        )
        self.reviews.set_author_identity(
            task_id,
            ReviewIdentity(task_id, lease.attempt_id, f"author-run-{task_id}", author_session,
                           spec.capability_class),
        )
        self.scheduler.verify_candidate(task_id)
        candidate = Candidate(
            task_id=task_id, attempt_id=lease.attempt_id, result_commit=result,
            base_commit=self.base, worktree=worktree, files=dict(files),
            author_session=author_session, reviewer_session=None, spec=spec,
        )
        if review:
            self.review(candidate, reviewer_session)
        return candidate

    def review(self, candidate: Candidate, reviewer_session: str) -> None:
        task_id = candidate.task_id
        reviewer = ReviewIdentity(
            f"review-{task_id}", f"review-{task_id}-attempt", f"reviewer-run-{task_id}",
            reviewer_session, "verifier",
        )
        assignment = self.reviews.assign(task_id, reviewer)
        binding = json.loads(self.store.scalar(
            "SELECT payload_json FROM artifacts WHERE artifact_id = ?",
            (f"candidate-binding:{task_id}:{candidate.attempt_id}",),
        ))
        verdict = {
            "artifact_type": "review-verdict",
            "review_id": f"{task_id}-review-1",
            "task_id": task_id,
            "attempt_id": candidate.attempt_id,
            "reviewer_role": "verifier",
            "reviewer_provider": "fixture-review-provider",
            "reviewer_model": "fixture-review-model",
            "reviewer_session_id": reviewer_session,
            "reviewer_run_id": reviewer.provider_run_id,
            "lease_id": assignment["lease_id"],
            "lease_epoch": assignment["lease_epoch"],
            "fencing_token": assignment["fencing_token"],
            "reviewed_result_commit": binding["result_commit"],
            "tree_hash": binding["tree_hash"],
            "diff_hash": binding["diff_hash"],
            "declared_base_commit": binding["base_commit"],
            "contract_hash": binding["contract_hash"],
            "context_hash": binding["context_hash"],
            "verification_evidence_hash": self.store.scalar(
                "SELECT content_hash FROM artifacts WHERE artifact_id = ?",
                (f"verification:{task_id}:{candidate.attempt_id}",),
            ),
            "findings": [],
            "verdict": "no_blocking_findings",
            "independence_attestation": {
                "author_session_id": candidate.author_session,
                "author_run_id": f"author-run-{task_id}",
                "distinct": True,
            },
        }
        self.reviews.ingest(task_id, verdict)
        candidate.reviewer_session = reviewer_session
        candidate.verdict = verdict

    # -- processes ----------------------------------------------------------

    def _env(self) -> dict[str, str]:
        return {**os.environ, "PYTHONPATH": str(AGENTCTL_DIR)}

    def cli(self, *args: str, timeout: float = 120) -> CliResult:
        """Run the ordinary ``python3 -m agentctl integrate`` command, for real."""
        done = subprocess.run(
            [sys.executable, "-m", "agentctl", "integrate", *args],
            cwd=self.root, env=self._env(), capture_output=True, text=True, timeout=timeout,
        )
        return CliResult(done.returncode, done.stdout, done.stderr)

    def spawn_cli(self, *args: str) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [sys.executable, "-m", "agentctl", "integrate", *args],
            cwd=self.root, env=self._env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )

    def run_until_killed(self, task_id: str | None, point: str) -> int:
        """SIMULATED crash: a real child process that dies with ``os._exit`` at ``point``.

        No cleanup runs in the child (no ``finally``, no lock release by us), so
        Git and SQLite are left exactly as a SIGKILL at that instant would leave
        them. The crash instant is injected; the process, Git and SQLite are real.
        """
        script = (
            "import os, sys\n"
            "from agentctl.integrate import integrate\n"
            "point = sys.argv[2]\n"
            "task = sys.argv[1] or None\n"
            "integrate(task, fault_hook=lambda p: os._exit(97) if p == point else None)\n"
        )
        done = subprocess.run(
            [sys.executable, "-c", script, task_id or "", point],
            cwd=self.root, env=self._env(), capture_output=True, text=True, timeout=120,
        )
        return done.returncode
