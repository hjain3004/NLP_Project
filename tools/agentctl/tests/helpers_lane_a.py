"""Shared fixtures for the Lane A provider-path tests.

* :func:`install_fake_claude` writes a **simulated** ``claude`` executable (a small Python
  script) whose behaviour is chosen by a ``scenario.json`` beside it. It records every
  call (argv, stdin, cwd, environment *names*) so tests can assert exactly what the
  control plane ran. Tests that use it are simulated and say so; they prove the control
  plane's handling of provider outcomes, never that a live provider works.
* :class:`LaneARepo` builds a disposable git repository (a shared clone of this repository,
  so every schema/policy file is real), commits a synthetic work package and task contract,
  registers them with the real :class:`PlanLoader`, and opens a real :class:`EventStore`.
"""

from __future__ import annotations

import copy
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from typing import Any, Mapping

from agentctl.hashing import sha256_of_json
from agentctl.plan import PlanLoader
from agentctl.scheduler import Scheduler
from agentctl.store import EventStore

SOURCE_ROOT = pathlib.Path(__file__).resolve().parents[3]
PACKAGE_PARENT = SOURCE_ROOT / "tools" / "agentctl"

_FAKE_CLAUDE = r'''#!@@PYTHON@@
import json, os, signal, subprocess, sys, time, uuid

here = os.path.dirname(os.path.abspath(__file__))
scenario = json.load(open(os.path.join(here, "scenario.json")))
args = sys.argv[1:]

if args[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": scenario.get("logged_in", True)}))
    sys.exit(0)

stdin = sys.stdin.read()
with open(os.path.join(here, "calls.jsonl"), "a") as log:
    log.write(json.dumps({"argv": args, "stdin": stdin, "cwd": os.getcwd(),
                           "env_names": sorted(os.environ)}) + "\n")

def flag(name):
    return args[args.index(name) + 1] if name in args else None

model = flag("--model")
session = scenario.get("session_override") or flag("--session-id") or str(uuid.uuid4())
mode = scenario.get("mode", "success")

if scenario.get("expire_lease_db"):
    import sqlite3
    connection = sqlite3.connect(scenario["expire_lease_db"])
    connection.execute("UPDATE leases SET expires_at = '2000-01-01T00:00:00.000000Z' WHERE active = 1")
    connection.commit()
    connection.close()

if mode == "sleep":
    grandchild = subprocess.Popen(["sleep", "300"])
    open(scenario["grandchild_pid_file"], "w").write(str(grandchild.pid))
    sys.stdout.write("partial-output-before-kill")
    sys.stdout.flush()
    time.sleep(300)
    sys.exit(0)
if mode == "exit_nonzero":
    sys.stderr.write("fake claude crashed\n")
    sys.exit(7)
if mode == "malformed":
    print("this is not json {")
    sys.exit(0)

usage = {"input_tokens": 11, "cache_creation_input_tokens": 200, "cache_read_input_tokens": 30,
          "output_tokens": 42}
if mode == "missing_usage":
    usage = {"input_tokens": 11, "output_tokens": 42}
model_usage = {model: {"inputTokens": 11, "canonicalModel": model}}
if mode == "model_mismatch":
    model_usage = {"claude-other-9": {"inputTokens": 1}}
if mode == "extra_model":
    model_usage["claude-helper-1"] = {"inputTokens": 1}

result = {
    "type": "result", "subtype": "success", "is_error": False, "terminal_reason": "completed",
    "api_error_status": None, "session_id": session, "uuid": scenario.get("result_uuid", str(uuid.uuid4())),
    "duration_ms": 1234, "total_cost_usd": 0.0123, "usage": usage, "modelUsage": model_usage,
    "result": "ok", "structured_output": scenario.get("structured_output"),
}
if mode == "unknown_model_exit0":
    result.update(is_error=True, terminal_reason="api_error", api_error_status=404, subtype="success",
                  modelUsage={}, structured_output=None, usage={},
                  result="There's an issue with the selected model (" + str(model) + ").")
elif mode == "auth_error":
    result.update(is_error=True, terminal_reason="api_error", api_error_status=401, modelUsage={},
                  structured_output=None, result="Invalid authentication credentials. Please run /login")
elif mode == "budget_error":
    result.update(is_error=True, subtype="error_max_budget_usd", terminal_reason="budget_exhausted",
                  structured_output=None, errors=["Reached maximum budget"])
    print(json.dumps(result)); sys.exit(1)
elif mode == "no_structured":
    result["structured_output"] = None
elif mode == "bad_session":
    result["session_id"] = "not-a-uuid"
elif mode == "not_completed":
    result["terminal_reason"] = "max_turns"
print(json.dumps(result))
sys.exit(scenario.get("exit_code", 0))
'''


class FakeClaude:
    def __init__(self, directory: pathlib.Path) -> None:
        self.directory = directory
        self.executable = directory / "claude"

    def set(self, **scenario: Any) -> None:
        (self.directory / "scenario.json").write_text(json.dumps(scenario), encoding="utf-8")

    def calls(self) -> list[dict[str, Any]]:
        log = self.directory / "calls.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def install_fake_claude(directory: pathlib.Path, **scenario: Any) -> FakeClaude:
    directory.mkdir(parents=True, exist_ok=True)
    fake = FakeClaude(directory)
    fake.executable.write_text(_FAKE_CLAUDE.replace("@@PYTHON@@", sys.executable), encoding="utf-8")
    fake.executable.chmod(0o755)
    fake.set(**scenario)
    return fake


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A killed-but-unreaped child is a zombie: not running.
    try:
        status = pathlib.Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return status.rsplit(")", 1)[1].split()[0] != "Z"


def git(cwd: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def proposal(path: str = "docs/lane-a/notes.md", content: str = "line one\nline two\n", **overrides: Any) -> dict:
    value = {
        "summary": "Add a line to the notes",
        "files": [{"path": path, "action": "write", "content": content}],
        "acceptance_criteria_addressed": ["The notes file gains the requested line"],
        "assumptions": ["Notes are plain text"],
        "unresolved_risks": [],
    }
    value.update(overrides)
    return value


class LaneARepo:
    """A disposable repository with one registered task and a real event store."""

    TASK_ID = "LANEA-001"
    WORK_PACKAGE_ID = "LANEA-WP"

    def __init__(
        self,
        test: unittest.TestCase,
        *,
        capability_class: str = "standard_implementation",
        risk: str = "low",
        writable: tuple[str, ...] = ("docs/lane-a/**",),
        prohibited: tuple[str, ...] = ("AppleBooks/**", "agent-system/schemas/**", "agent-system/policies/**"),
        maximum_attempts: int = 2,
        deterministic_checks: tuple[str, ...] = ("test -d docs/lane-a",),
        registry: Mapping[str, tuple[str, str]] | None = None,
        symlink_target: pathlib.Path | None = None,
    ) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        test.addCleanup(self.temporary.cleanup)
        base = pathlib.Path(self.temporary.name).resolve()
        self.root = base / "repository"
        git(base, "clone", "--quiet", "--shared", str(SOURCE_ROOT), str(self.root))
        git(self.root, "config", "user.name", "fixture")
        git(self.root, "config", "user.email", "fixture@localhost.invalid")
        git(self.root, "config", "commit.gpgsign", "false")

        if registry:
            path = self.root / "agent-system/routing/capability-registry.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            for entry in document["entries"]:
                if entry["capability_class"] in registry:
                    entry["provider"], entry["model_id"] = registry[entry["capability_class"]]
            path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

        notes = self.root / "docs/lane-a"
        notes.mkdir(parents=True, exist_ok=True)
        (notes / "notes.md").write_text("original line\n", encoding="utf-8")
        (self.root / "docs/lane-a-outside.md").write_text("outside scope\n", encoding="utf-8")
        if symlink_target is not None:
            os.symlink(symlink_target, notes / "escape")
        git(self.root, "add", "-A")
        git(self.root, "commit", "--quiet", "-m", "fixture: base for Lane A task")
        self.base_commit = git(self.root, "rev-parse", "HEAD")

        wp_dir = self.root / f"agent-system/work/LANEA"
        (wp_dir / "tasks").mkdir(parents=True, exist_ok=True)
        work_package = json.loads(
            (SOURCE_ROOT / "agent-system/work/DRY-RUN/work-package.json").read_text(encoding="utf-8")
        )
        work_package["work_package_id"] = self.WORK_PACKAGE_ID
        (wp_dir / "work-package.json").write_text(json.dumps(work_package, indent=2) + "\n", encoding="utf-8")
        contract = json.loads(
            (SOURCE_ROOT / "agent-system/work/DRY-RUN/tasks/DRY-001.json").read_text(encoding="utf-8")
        )
        contract["identity"].update(
            task_id=self.TASK_ID, work_package_id=self.WORK_PACKAGE_ID, base_commit=self.base_commit
        )
        contract["intent"]["objective"] = "Append one synthetic line to the Lane A notes file"
        contract["intent"]["acceptance_behavior"] = ["docs/lane-a/notes.md gains a second line"]
        contract["scope"].update(
            readable_paths=["AGENTS.md", "docs/lane-a/notes.md"],
            writable_paths=list(writable),
            prohibited_paths=list(prohibited),
        )
        contract["routing"].update(capability_class=capability_class, risk=risk)
        contract["verification"]["deterministic_checks"] = list(deterministic_checks)
        contract["budgets"]["maximum_attempts"] = maximum_attempts
        contract["budgets"]["maximum_artifact_corrections"] = 0
        unhashed = copy.deepcopy(contract)
        unhashed["identity"].pop("contract_hash")
        contract["identity"]["contract_hash"] = sha256_of_json(unhashed)
        self.contract = contract
        (wp_dir / "tasks" / f"{self.TASK_ID}.json").write_text(
            json.dumps(contract, indent=2) + "\n", encoding="utf-8"
        )
        git(self.root, "add", "agent-system/work/LANEA")
        git(self.root, "commit", "--quiet", "-m", "fixture: Lane A work package")

        self.database = self.root / "agent-system/runtime/agentctl.sqlite3"
        self.store = EventStore(self.database, repository_root=self.root)
        PlanLoader(self.root, self.store).validate(wp_dir / "work-package.json")
        self.scheduler = Scheduler(self.store)

    def state(self) -> str:
        return str(self.store.status(self.TASK_ID)["state"])

    def cli(self, *args: str, path_prefix: pathlib.Path | None = None) -> subprocess.CompletedProcess[str]:
        """Run the real ``python3 -m agentctl`` CLI inside this repository."""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(PACKAGE_PARENT)
        if path_prefix is not None:
            env["PATH"] = f"{path_prefix}{os.pathsep}{env['PATH']}"
        return subprocess.run(
            [sys.executable, "-m", "agentctl", *args],
            cwd=self.root, env=env, capture_output=True, text=True, timeout=120,
        )
