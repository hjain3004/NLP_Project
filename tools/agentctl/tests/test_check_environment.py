"""The enforced candidate-check boundary and the single check-environment contract.

Ops lane A, task 1 and task 2 (reports/ops-lane-a-1.md). The audit and three reviews proved that
candidate-controlled checks ran at the control-plane uid with absolute-path reach to the event store,
credentials, other worktrees and the shared ``.git``, and with network access; verify and integrate
also ran in different environments (a ``.git`` link versus none).

Everything here is real: real Git repositories, the real event store, the real scheduler and
integrator, real ``runc`` containers, real TCP sockets and real processes. Planted canaries are
harmless files the test creates itself; no real credential is read or written. The tests that need
``runc`` are skipped, labelled, when the probe says it is unavailable on the host; the fail-closed
tests (no runc) are never skipped.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from agentctl import sandbox
from agentctl import scheduler as scheduler_module
from agentctl.errors import PermissionViolation
from agentctl.integrate import IntegrationSettings, integrate

from tests.helpers_lane_b import BRANCH, Lab

NEEDS_RUNC = unittest.skipUnless(
    sandbox.probe()["available"],
    "runc sandbox unavailable on this host (sandbox-boundary tests only; the fail-closed tests still run)",
)
FILES = {"docs/hardening.txt": "synthetic\n"}
RAN = "PROBE-RAN\n"
RAN_HASH = hashlib.sha256(RAN.encode()).hexdigest()


def _alive_with(token: bytes) -> list[int]:
    """Live (non-zombie) host processes whose command line carries ``token``."""
    found = []
    for name in os.listdir("/proc"):
        if not name.isdigit() or int(name) == os.getpid():
            continue
        try:
            command = pathlib.Path(f"/proc/{name}/cmdline").read_bytes()
            state = pathlib.Path(f"/proc/{name}/stat").read_bytes().rsplit(b")", 1)[1].split()[0]
        except OSError:
            continue
        if token in command and state != b"Z":
            found.append(int(name))
    return found


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("AGENTCTL_CHECK_ISOLATION", None)
        os.environ.pop("AGENTCTL_RUNC", None)
        self.lab = Lab()
        self.addCleanup(self.lab.close)
        self.host = pathlib.Path(tempfile.mkdtemp(prefix="check-env-host-only-"))
        self.addCleanup(shutil.rmtree, self.host, True)

    # -- helpers ----------------------------------------------------------------

    def candidate(self, task_id: str, checks: tuple[str, ...], *, files=FILES, risk: str = "low"):
        """A real candidate left in state ``candidate`` (verification not yet run)."""
        return self.lab.candidate(task_id, files, checks=checks, risk=risk, verify=False)

    def evidence(self, task_id: str) -> dict:
        (row,) = self.lab.artifacts("verification-evidence", task_id)
        return row["payload"]

    def verified(self, task_id: str, checks: tuple[str, ...], **kwargs):
        candidate = self.candidate(task_id, checks, **kwargs)
        self.lab.scheduler.verify_candidate(task_id)
        return candidate

    def reviewed(self, task_id: str, checks: tuple[str, ...], **kwargs):
        candidate = self.verified(task_id, checks, **kwargs)
        self.lab.review(candidate, f"reviewer-{task_id}")
        return candidate

    def assert_probe_ran_and_held(self, evidence: dict) -> None:
        """Every check exited 0 (the breach branch was not taken) AND printed the control marker."""
        self.assertIs(evidence["passed"], True, evidence)
        for item in evidence["checks"]:
            self.assertEqual(item["exit_code"], 0, item)
            self.assertEqual(item["stdout_hash"], RAN_HASH, item)  # positive control: it really ran

    def both_stages(self, task_id: str, checks: tuple[str, ...], **kwargs) -> tuple[dict, dict, dict]:
        """verify, independent review, integrate. Returns (verify evidence, integrate result, intent)."""
        candidate = self.verified(task_id, checks, **kwargs)
        evidence = self.evidence(task_id)
        self.assertEqual(self.lab.state(task_id), "independent_review", evidence)
        self.lab.review(candidate, f"reviewer-{task_id}")
        result = integrate(task_id, store=self.lab.store)
        intent = self.lab.artifacts("integration-intent", task_id)
        return evidence, result, (intent[0]["payload"] if intent else {})


# ----------------------------------------------------------------------------------------------
# Task 1: the boundary itself, through the real verify and integrate flows.
# ----------------------------------------------------------------------------------------------


@NEEDS_RUNC
class CheckCannotReachTheHostTests(_Case):
    def test_checks_cannot_read_a_planted_host_only_canary_secret(self) -> None:
        """A check named a host-only absolute path (a harmless canary) and read it; the sandbox has no such path."""
        directory = self.host / "credentials"
        directory.mkdir()
        canary = directory / "token.canary"
        canary.write_text("SYNTHETIC-CANARY-NOT-A-SECRET\n")
        check = (
            f"if [ -e {canary} ] || cat {canary} >/dev/null 2>&1; then echo BREACH-FILE; exit 1; fi; "
            f"if ls {directory} >/dev/null 2>&1; then echo BREACH-DIR; exit 1; fi; echo PROBE-RAN"
        )

        evidence, result, intent = self.both_stages("T-CAN", (check,))

        self.assert_probe_ran_and_held(evidence)  # verification stage
        self.assertEqual(result["outcome"], "integrated", result)  # integration stage reran the same probe
        self.assertTrue(all(g["result"] == "pass" for g in intent["gates"]), intent["gates"])
        self.assertEqual(canary.read_text(), "SYNTHETIC-CANARY-NOT-A-SECRET\n")

    def test_checks_cannot_modify_or_delete_the_control_plane_store(self) -> None:
        """A check tried `rm` and `echo >>` on the real event-store file by absolute path."""
        database = self.lab.root / "agent-system" / "runtime" / "agentctl.sqlite3"
        marker = "AGENTCTL-TAMPER-MARKER"
        # The command is stored in the event store, so the marker must not appear in it literally.
        check = (
            f"rm -f {database} {database}-wal {database}-shm {database}-journal 2>/dev/null; "
            f"if (echo AGENTCTL-TAMPER-$(echo MARKER) >> {database}) 2>/dev/null; then echo BREACH-APPEND; exit 1; fi; "
            f"if [ -e {database} ]; then echo BREACH-VISIBLE; exit 1; fi; echo PROBE-RAN"
        )

        evidence, result, _intent = self.both_stages("T-DB", (check,))

        self.assert_probe_ran_and_held(evidence)
        self.assertEqual(result["outcome"], "integrated", result)
        self.assertTrue(database.exists())
        self.assertNotIn(marker.encode(), database.read_bytes())
        with sqlite3.connect(database) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_checks_cannot_make_a_network_request_to_the_host_or_the_outside(self) -> None:
        """A local TCP server on the host's 127.0.0.1 and unroutable (TEST-NET) addresses are all unreachable."""
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(8)
        port = server.getsockname()[1]
        accepted: list[int] = []

        def serve() -> None:
            while True:
                try:
                    connection, _ = server.accept()
                except OSError:
                    return
                accepted.append(1)
                connection.close()

        threading.Thread(target=serve, daemon=True).start()
        self.addCleanup(server.close)
        check = (
            "python3 - <<'PY'\n"
            "import socket, sys\n"
            f"for host, port in (('127.0.0.1', {port}), ('192.0.2.1', 80), ('203.0.113.9', 443)):\n"
            "    try:\n"
            "        socket.create_connection((host, port), timeout=1.5).close()\n"
            "    except OSError:\n"
            "        continue\n"
            "    print('BREACH', host)\n"
            "    sys.exit(1)\n"
            "print('PROBE-RAN')\n"
            "PY"
        )

        evidence, result, _intent = self.both_stages("T-NET", (check,))

        self.assert_probe_ran_and_held(evidence)
        self.assertEqual(result["outcome"], "integrated", result)
        self.assertEqual(accepted, [], "the host-side server received a connection from a check")

    def test_checks_cannot_see_the_checkout_home_directories_other_worktrees_or_the_shared_git(self) -> None:
        """The listing a check sees contains only the runtime subset and its own tree."""
        home = self.host / "home" / "user"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / ".credentials.json").write_text('{"synthetic": true}\n')
        (home / ".cashbooks").mkdir()
        other = self.verified("T-OTHER", ("true",))  # a real, separate leased worktree
        forbidden = [
            self.lab.root, self.lab.root / ".git", self.lab.root / "agent-system" / "runtime",
            self.lab.root / "agent-system", other.worktree, home / ".claude", home / ".cashbooks",
            "/root", "/home", "/var/run/docker.sock", "/run/docker.sock", "/run/agentctl-runc",
        ]
        tests = " ".join(f"[ -e {path} ] && echo BREACH:{path};" for path in forbidden)
        check = (
            f"{tests} "
            "for entry in /*; do case \"${entry#/}\" in bin|dev|etc|lib|lib64|proc|sbin|tmp|usr|work) ;; "
            "*) echo BREACH-ROOT:$entry;; esac; done; "
            "found=$(find / -name agentctl.sqlite3 -not -path '/proc/*' 2>/dev/null | head -1); "
            "[ -z \"$found\" ] || echo BREACH-STORE:$found; "
            "ls -a /work | grep -qx docs || echo BREACH-NO-TREE; "
            "echo PROBE-RAN"
        )
        shell = f"out=$({check}); printf '%s\\n' \"$out\" | grep -q BREACH && {{ echo \"$out\"; exit 1; }}; echo PROBE-RAN"

        evidence, result, _intent = self.both_stages("T-LS", (shell,))

        self.assert_probe_ran_and_held(evidence)
        self.assertEqual(result["outcome"], "integrated", result)


@NEEDS_RUNC
class ChecksCannotInfluenceEachOtherTests(_Case):
    """Review finding (medium): checks used to share one /work tree and one synthetic repository, so an early
    check could rewrite the tree and amend the synthetic commit and a later check would then pass on content
    that was never committed. Every check now starts from a freshly materialised pristine tree."""

    PLANT = "echo good > lint.txt; git add -A; git -c user.name=x -c user.email=x@x.invalid commit -q --amend -m planted"
    LATER = "grep -qx good lint.txt && git diff --quiet HEAD"

    def run_stage(self, label: str, checks: tuple[str, ...]) -> dict:
        from agentctl.check_environment import run_candidate_checks

        candidate = self.candidate(f"T-ISO-{label}", ("true",))
        return run_candidate_checks(
            self.lab.root, candidate.result_commit, checks, label=label, timeout=60.0,
            spec_fields={"task_id": candidate.task_id},
        )

    def test_a_check_cannot_rewrite_the_tree_or_the_synthetic_repo_for_a_later_check_in_either_stage(self) -> None:
        for label in ("verify", "integrate"):
            with self.subTest(stage=label):
                outcome = self.run_stage(label, (self.PLANT, self.LATER))

                first, second = outcome["checks"]
                self.assertEqual(first["result"], "pass", first)
                self.assertEqual(second["result"], "fail", "a later check passed on content an earlier check planted")
                self.assertEqual(second["reason"], "exit_nonzero")
                self.assertIs(outcome["passed"], False)

    def test_neither_file_state_nor_repository_state_survives_into_the_next_check(self) -> None:
        plant = (
            "echo scratch > scratch.txt; rm -f docs/hardening.txt; mkdir -p planted/dir; "
            "git config user.name changed; git checkout -q -b planted-branch; git tag planted-tag; "
            "echo dirty >> docs/hardening.txt"
        )
        pristine = (
            "test ! -e scratch.txt && test ! -d planted && test \"$(cat docs/hardening.txt)\" = synthetic "
            "&& git diff --quiet HEAD && test -z \"$(git status --porcelain)\" "
            "&& test \"$(git rev-parse --abbrev-ref HEAD)\" = main && test -z \"$(git tag)\" "
            "&& test \"$(git config user.name)\" = 'agentctl check-environment' "
            "&& test \"$(git rev-list --count HEAD)\" = 1"
        )

        outcome = self.run_stage("verify", (plant, pristine, pristine))

        self.assertEqual([item["result"] for item in outcome["checks"]], ["pass", "pass", "pass"], outcome["checks"])

    def test_a_check_may_still_write_scratch_files_and_commit_within_itself(self) -> None:
        within = (
            "echo scratch > scratch.txt && test \"$(cat scratch.txt)\" = scratch && git add -A "
            "&& git -c user.name=x -c user.email=x@x.invalid commit -q -m inner && git diff --quiet HEAD"
        )

        outcome = self.run_stage("verify", (within,))

        self.assertIs(outcome["passed"], True, outcome["checks"])

    def test_the_reviewers_command_pair_fails_in_a_real_verification_and_nothing_advances(self) -> None:
        self.candidate("T-ISO-V", (self.PLANT, self.LATER))

        result = self.lab.scheduler.verify_candidate("T-ISO-V")

        self.assertNotEqual(result["state"], "independent_review", result)
        evidence = self.evidence("T-ISO-V")
        self.assertIs(evidence["passed"], False)
        self.assertEqual([item["result"] for item in evidence["checks"]], ["pass", "fail"])


@NEEDS_RUNC
class TimeoutKillsEverythingTests(_Case):
    def test_a_timed_out_check_leaves_no_process_and_no_container(self) -> None:
        """setsid / double-fork stragglers die with the PID namespace; `runc list` is empty afterwards."""
        token = "31.4159"
        check = (
            f"setsid sh -c 'exec sleep {token}' & "
            f"setsid sh -c '(sleep {token} &); sleep {token}' & "
            f"sleep 60"
        )
        self.addCleanup(lambda: [os.kill(pid, 9) for pid in _alive_with(token.encode()) if os.path.exists(f"/proc/{pid}")])
        self.candidate("T-TO", (check,))
        started = time.monotonic()

        with mock.patch.object(scheduler_module, "CHECK_TIMEOUT_SECONDS", 2.0):
            self.lab.scheduler.verify_candidate("T-TO")

        self.assertLess(time.monotonic() - started, 25)
        evidence = self.evidence("T-TO")
        (item,) = evidence["checks"]
        self.assertEqual(item["isolation"], "runc", item)
        self.assertEqual(item["exit_code"], 124, item)
        self.assertIs(item["timed_out"], True, item)
        self.assertIs(evidence["passed"], False)
        self.assertEqual(_alive_with(token.encode()), [])
        listing = subprocess.run(
            [sandbox.runc_binary() or "runc", "--root", sandbox.state_root(), "list", "-q"],
            capture_output=True, text=True,
        )
        self.assertEqual(listing.stdout.split(), [], "a container was left behind")

    def test_a_background_process_does_not_survive_a_check_that_exits_normally(self) -> None:
        token = "27.1828"
        self.addCleanup(lambda: [os.kill(pid, 9) for pid in _alive_with(token.encode()) if os.path.exists(f"/proc/{pid}")])
        self.candidate("T-BG", (f"setsid sleep {token} & (sleep {token} &); echo ok",))

        self.lab.scheduler.verify_candidate("T-BG")

        self.assertIs(self.evidence("T-BG")["passed"], True)
        self.assertEqual(_alive_with(token.encode()), [])


# ----------------------------------------------------------------------------------------------
# Fail closed.
# ----------------------------------------------------------------------------------------------


class FailClosedTests(_Case):
    """Never skipped: these run exactly where the sandbox is missing."""

    def test_verify_refuses_before_any_transition_when_the_sandbox_is_unavailable(self) -> None:
        marker = self.host / "ran.marker"
        self.candidate("T-FC", (f"touch {marker}",))
        events_before = len(self.lab.events())
        os.environ["AGENTCTL_RUNC"] = "/nonexistent/agentctl-test/runc"

        with self.assertRaises(PermissionViolation) as raised:
            self.lab.scheduler.verify_candidate("T-FC")

        self.assertEqual(raised.exception.context["reason_code"], "sandbox_unavailable")
        self.assertEqual(self.lab.state("T-FC"), "candidate")  # no transition happened
        self.assertEqual(len(self.lab.events()), events_before)
        self.assertEqual(self.lab.artifacts("verification-evidence", "T-FC"), [])
        self.assertFalse(marker.exists(), "the check ran without a sandbox")

    @NEEDS_RUNC
    def test_integrate_refuses_with_sandbox_unavailable_and_moves_nothing(self) -> None:
        """A candidate verified while the sandbox worked; then the sandbox disappears. No fallback, no change."""
        marker = self.host / "ran.marker"
        # `|| true`: harmless in the sandbox (no such path), but it would create the marker if run unsandboxed.
        candidate = self.reviewed("T-FI", (f"touch {marker} 2>/dev/null || true",))
        self.assertFalse(marker.exists())
        events_before = len(self.lab.events())
        os.environ["AGENTCTL_RUNC"] = "/nonexistent/agentctl-test/runc"

        run = self.lab.cli("T-FI")

        self.assertEqual(run.code, 1, run.stdout + run.stderr)
        self.assertEqual(run.json["reason_code"], "sandbox_unavailable", run.json)
        self.assertIsNone(self.lab.branch_head(BRANCH))  # no branch move
        self.assertEqual(self.lab.state(candidate.task_id), "integration_ready")  # no state advance
        for kind in ("integration-record", "integration-intent", "integration-rejection", "integration-lease"):
            self.assertEqual(self.lab.artifacts(kind, "T-FI"), [], kind)
        self.assertEqual(len(self.lab.events()), events_before)
        self.assertFalse(marker.exists(), "the check ran without a sandbox")
        self.assertEqual(self.lab.leftover_integration_worktrees(), [])

    def test_the_verify_command_says_why_it_refused_and_exits_nonzero(self) -> None:
        self.candidate("T-CLI", ("true",))
        env = {**os.environ, "PYTHONPATH": str(pathlib.Path(__file__).resolve().parents[1]),
               "AGENTCTL_RUNC": "/nonexistent/agentctl-test/runc"}

        done = subprocess.run(
            [sys.executable, "-m", "agentctl", "verify", "T-CLI"], cwd=self.lab.root, env=env,
            capture_output=True, text=True, timeout=120,
        )

        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(json.loads(done.stdout)["reason_code"], "sandbox_unavailable")
        self.assertEqual(self.lab.state("T-CLI"), "candidate")

    def test_an_unrecognised_isolation_selector_is_refused_not_ignored(self) -> None:
        self.candidate("T-BAD", ("true",))
        os.environ["AGENTCTL_CHECK_ISOLATION"] = "none"

        with self.assertRaises(PermissionViolation) as raised:
            self.lab.scheduler.verify_candidate("T-BAD")

        self.assertEqual(raised.exception.context["reason_code"], "check_isolation_invalid")
        self.assertEqual(self.lab.state("T-BAD"), "candidate")


# ----------------------------------------------------------------------------------------------
# Task 2: one check-environment contract for verify and integrate.
# ----------------------------------------------------------------------------------------------

GIT_DEPENDENT = (
    "git rev-parse --show-toplevel >/dev/null && git ls-files | grep -q docs/hardening.txt "
    "&& test -z \"$(git status --porcelain)\" && test -n \"$AGENTCTL_CANDIDATE_COMMIT\" "
    "&& test \"$(git rev-parse HEAD)\" != \"$AGENTCTL_CANDIDATE_COMMIT\" && test \"$PWD\" = /work "
    "&& test \"$HOME\" = /tmp && test -z \"$PYTHONPATH\" && test -z \"$ANTHROPIC_API_KEY\""
)


@NEEDS_RUNC
class OneContractTests(_Case):
    def test_the_same_checks_give_the_same_verdict_and_the_same_environment_in_verify_and_integrate(self) -> None:
        """A legitimately git-dependent check passes at BOTH stages (it used to fail only at integration)."""
        checks = (
            GIT_DEPENDENT,
            "grep -q synthetic docs/hardening.txt",
            "test \"$(sh -c 'echo $0' x)\" = x && command -v python3 >/dev/null",
        )
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "synthetic-not-real", "PYTHONPATH": "/nope"}):
            evidence, result, intent = self.both_stages("T-ONE", checks)

        self.assertEqual(result["outcome"], "integrated", result)
        verify_verdicts = [(c["command"], c["exit_code"]) for c in evidence["checks"]]
        integrate_verdicts = [
            (g["gate"], g["exit_code"]) for g in intent["gates"] if g["gate"] in checks
        ]
        self.assertEqual(verify_verdicts, integrate_verdicts)
        self.assertEqual(verify_verdicts, [(c, 0) for c in checks])
        self.assertTrue(evidence["environment"], "verify evidence carries the environment contract")
        self.assertEqual(evidence["environment"], intent["check_environment"])
        environment = evidence["environment"]
        self.assertEqual(environment["isolation"], "runc")
        self.assertEqual(environment["network"], "none")
        self.assertEqual(environment["git_mode"], "synthetic-repository")
        self.assertEqual(environment["shell"], "/bin/sh -c")
        self.assertEqual(environment["cwd"], "/work")
        self.assertEqual(
            environment["env_allowlist"],
            ["AGENTCTL_CANDIDATE_COMMIT", "HOME", "LANG", "PATH", "PYTHONDONTWRITEBYTECODE", "TZ"],
        )
        self.assertRegex(environment["runtime_hash"], r"^[0-9a-f]{64}$")
        self.assertIn("contract", environment)
        self.assertIn("contract_version", environment)

    def test_a_failing_affected_check_blocks_integration_and_leaves_the_branch_and_task_untouched(self) -> None:
        """Passes on each candidate's own tree (verify), fails on the merged tree: exit 1 at integration."""
        check = "test $(ls src | wc -l) -le 1"
        self.reviewed("T-F1", (check,), files={"src/one.txt": "1\n"})
        self.reviewed("T-F2", (check,), files={"src/two.txt": "2\n"})
        self.assertIs(self.evidence("T-F2")["passed"], True)
        self.assertEqual(self.lab.cli("T-F1").code, 0)
        head = self.lab.branch_head()

        run = self.lab.cli("T-F2")

        self.assertEqual(run.code, 1)
        self.assertEqual(run.json["reason_code"], "affected_check_failed")
        self.assertEqual(self.lab.branch_head(), head)
        self.assertNotEqual(self.lab.state("T-F2"), "completed")
        self.assertEqual(self.lab.artifacts("integration-record", "T-F2"), [])
        failed = [g for g in run.json["details"]["gates"] if g["result"] == "fail"]
        self.assertEqual([(g["gate"], g["exit_code"]) for g in failed], [(check, 1)])

    def test_a_timed_out_check_has_the_same_vocabulary_in_both_stages(self) -> None:
        """exit 124 + timed_out + reason timed_out + isolation, in verify evidence and integration gates alike."""
        self.reviewed("T-H1", ("true",), files={"src/one.txt": "1\n"})
        self.assertEqual(self.lab.cli("T-H1").code, 0)
        hang = "if [ -e src/one.txt ] && [ -e src/two.txt ]; then sleep 30; fi"  # only the merged tree hangs
        hanging = self.candidate("T-H2", (hang,), files={"src/two.txt": "2\n"})
        self.candidate("T-HV", ("sleep 30",), files={"src/three.txt": "3\n"})
        with mock.patch.object(scheduler_module, "CHECK_TIMEOUT_SECONDS", 1.5):
            self.lab.scheduler.verify_candidate("T-H2")
            self.lab.scheduler.verify_candidate("T-HV")
        self.assertIs(self.evidence("T-H2")["passed"], True)
        (verify_item,) = self.evidence("T-HV")["checks"]
        self.lab.review(hanging, "reviewer-H2")

        result = integrate("T-H2", store=self.lab.store, settings=IntegrationSettings(check_timeout=1.5))

        self.assertEqual(result["reason_code"], "affected_check_failed", result)
        gate = result["details"]["gates"][-1]
        keys = ("exit_code", "timed_out", "reason", "isolation")
        self.assertEqual({k: gate[k] for k in keys}, {k: verify_item[k] for k in keys})
        self.assertEqual((gate["exit_code"], gate["timed_out"], gate["reason"]), (124, True, "timed_out"))


@NEEDS_RUNC
class IsolationSelectionTests(_Case):
    def test_default_isolation_is_runc_and_the_evidence_says_so(self) -> None:
        self.candidate("T-DEF", ("true",))

        self.lab.scheduler.verify_candidate("T-DEF")

        environment = self.evidence("T-DEF")["environment"]
        self.assertEqual(environment["isolation"], "runc")
        self.assertEqual(environment["network"], "none")

    def test_legacy_unsafe_is_explicit_recorded_in_the_evidence_and_still_works_for_low_and_medium_risk(self) -> None:
        os.environ["AGENTCTL_CHECK_ISOLATION"] = "legacy-unsafe"
        marker = self.host / "legacy-ran.marker"
        for task_id, risk in (("T-LOW", "low"), ("T-MED", "medium")):
            self.candidate(task_id, (f"touch {marker}",), risk=risk)

            result = self.lab.scheduler.verify_candidate(task_id)

            self.assertEqual(result["state"], "independent_review")
            environment = self.evidence(task_id)["environment"]
            self.assertEqual(environment["isolation"], "legacy-unsafe")
            self.assertEqual(environment["network"], "host")
        self.assertTrue(marker.exists(), "legacy-unsafe is the unsandboxed runner: it can write host paths")

    def test_legacy_unsafe_is_refused_for_high_and_critical_risk_before_any_check_runs(self) -> None:
        os.environ["AGENTCTL_CHECK_ISOLATION"] = "legacy-unsafe"
        marker = self.host / "must-not-run.marker"
        for task_id, risk in (("T-HI", "high"), ("T-CR", "critical")):
            self.candidate(task_id, (f"touch {marker}",), risk=risk)

            with self.assertRaises(PermissionViolation) as raised:
                self.lab.scheduler.verify_candidate(task_id)

            self.assertEqual(raised.exception.context["reason_code"], "gate_risk_not_permitted_until_sandbox_reviewed")
            self.assertEqual(self.lab.state(task_id), "candidate")
        self.assertFalse(marker.exists())

    def test_high_and_critical_risk_are_refused_even_under_the_sandbox_before_any_transition_event_or_check(self) -> None:
        """Coordinator veto: the sandbox boundary has had one review; high/critical stay refused until a further
        independent review and an owner decision. Allow-list: unknown risk is refused too."""
        marker = self.host / "must-not-run.marker"
        cases = (("high", "gate_risk_not_permitted_until_sandbox_reviewed"),
                 (" CRITICAL", "gate_risk_not_permitted_until_sandbox_reviewed"),
                 ("bogus", "unsandboxed_gate_unknown_risk"))
        for index, (risk, reason) in enumerate(cases):
            task_id = f"T-HRS{index}"
            self.candidate(task_id, (f"touch {marker}",), risk=risk)
            events_before = len(self.lab.events(task_id=task_id))

            with self.assertRaises(PermissionViolation) as raised:
                self.lab.scheduler.verify_candidate(task_id)

            self.assertEqual(raised.exception.context["reason_code"], reason)
            self.assertEqual(self.lab.state(task_id), "candidate")
            self.assertEqual(len(self.lab.events(task_id=task_id)), events_before)
            self.assertEqual(self.lab.artifacts("verification-evidence", task_id), [])
        self.assertFalse(marker.exists(), "a check ran for a task whose risk is not permitted")

    def test_integration_refuses_a_high_risk_task_under_the_sandbox_without_running_gates_or_moving_the_branch(self) -> None:
        """SIMULATED: the contract risk is raised after verification (as if an older build had verified it)."""
        marker = self.host / "integration-gate-ran.marker"
        self.lab.candidate("T-HRI", FILES, checks=(f"touch {marker}; true",))
        self.lab.tamper(
            "UPDATE tasks SET payload_json = json_set(payload_json, '$.risk', 'high') WHERE task_id = ?",
            ("T-HRI",),
        )

        result = integrate("T-HRI", store=self.lab.store)

        self.assertEqual(result["outcome"], "rejected", result)
        self.assertEqual(result["reason_code"], "gate_risk_not_permitted_until_sandbox_reviewed", result)
        self.assertIsNone(self.lab.branch_head(BRANCH))
        self.assertEqual(self.lab.state("T-HRI"), "integration_ready")
        self.assertFalse(marker.exists(), "an integration gate ran for a high-risk task")

    def test_integration_records_legacy_unsafe_in_its_intent_when_selected(self) -> None:
        os.environ["AGENTCTL_CHECK_ISOLATION"] = "legacy-unsafe"
        evidence, result, intent = self.both_stages("T-LEG", ("true",))

        self.assertEqual(result["outcome"], "integrated", result)
        self.assertEqual(evidence["environment"]["isolation"], "legacy-unsafe")
        self.assertEqual(intent["check_environment"]["isolation"], "legacy-unsafe")


if __name__ == "__main__":
    unittest.main()
