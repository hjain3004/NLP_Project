"""Gate runner: hard deadline, no surviving descendants, bounded output, no reach into the integrator.

Regression tests for the second independent review of Lane B (findings F1a and F2 in
reports/lane-b-3.md). The gates are real shell commands run by the real runner; the
hostile behaviour is written by the test to model a hostile gate.
"""

from __future__ import annotations

import ctypes
import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

from agentctl.integration_queue import run_gate_commands

AGENTCTL_DIR = pathlib.Path(__file__).resolve().parents[1]
LINUX = sys.platform.startswith("linux")


def _alive_with(token: bytes) -> list[int]:
    """Live (non-zombie) processes whose command line carries ``token``."""
    found = []
    for name in os.listdir("/proc"):
        if not name.isdigit() or int(name) == os.getpid():
            continue
        try:
            with open(f"/proc/{name}/cmdline", "rb") as handle:
                command = handle.read()
            with open(f"/proc/{name}/stat", "rb") as handle:
                state = handle.read().rsplit(b")", 1)[1].split()[0]
        except OSError:
            continue
        if token in command and state != b"Z":
            found.append(int(name))
    return found


def _kill(pids: list[int]) -> None:
    for pid in pids:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


@unittest.skipUnless(LINUX, "descendant tracking and rlimits are Linux-specific")
class GateDescendantTests(unittest.TestCase):
    """Default path (the integrator's): user+PID namespace where available."""

    def runner(self, command: str, directory: str, timeout: float) -> list[dict]:
        return run_gate_commands([command], cwd=directory, timeout=timeout)

    def run_one(self, command: str, token: bytes, timeout: float = 10.0) -> tuple[dict, float, list[int]]:
        self.addCleanup(lambda: _kill(_alive_with(token)))
        with tempfile.TemporaryDirectory() as directory:
            started = time.monotonic()
            (gate,) = self.runner(command, directory, timeout)
            elapsed = time.monotonic() - started
        return gate, elapsed, _alive_with(token)

    def test_setsid_descendant_that_drops_the_gate_marker_is_killed_and_fails_the_gate(self) -> None:
        """F2: `setsid env -u AGENTCTL_GATE_ID sleep & exit 0` returned pass in 0.03 s and the sleep survived."""
        gate, elapsed, survivors = self.run_one(
            "setsid env -u AGENTCTL_GATE_ID sleep 41.101 & exit 0", b"41.101"
        )

        self.assertEqual(survivors, [], "a gate descendant outlived the gate")
        self.assertEqual(gate["result"], "fail", gate)
        self.assertEqual(gate.get("reason"), "gate_left_processes", gate)
        self.assertGreaterEqual(gate.get("left_processes", 0), 1, gate)
        self.assertLess(elapsed, 5.0)

    def test_double_forked_descendant_with_a_cleared_environment_is_killed(self) -> None:
        """F2: `(setsid env -i sh -c 'sleep' &); exit 0` orphans a marker-less process."""
        gate, _elapsed, survivors = self.run_one(
            "(setsid env -i /bin/sh -c 'sleep 41.102' &) ; exit 0", b"41.102"
        )

        self.assertEqual(survivors, [])
        self.assertEqual(gate["result"], "fail", gate)
        self.assertEqual(gate.get("reason"), "gate_left_processes", gate)

    def test_descendants_of_a_timed_out_gate_are_killed_at_the_deadline(self) -> None:
        """Deadline: the shell hangs and its marker-less, setsid descendant must die with it."""
        gate, elapsed, survivors = self.run_one(
            "setsid env -u AGENTCTL_GATE_ID sleep 41.103 & sleep 30", b"41.103", timeout=1.0
        )

        self.assertEqual(survivors, [])
        self.assertTrue(gate["timed_out"], gate)
        self.assertIsNone(gate["exit_code"])
        self.assertEqual(gate["result"], "fail")
        self.assertLess(elapsed, 5.0)

    def test_runaway_output_is_bounded_and_fails_the_gate(self) -> None:
        """F2: a writer to the gate's stdout file reached 2.99 GB; output is now capped by RLIMIT_FSIZE."""
        gate, _elapsed, _survivors = self.run_one(
            "head -c 100000000 /dev/zero; exit 0", b"100000000"
        )

        self.assertEqual(gate["result"], "fail", gate)
        self.assertEqual(gate.get("reason"), "output_limit_exceeded", gate)
        self.assertLessEqual(gate["stdout_bytes"], 64 * 1024 * 1024)

    def test_orphaned_endless_writer_is_killed_and_its_output_bounded(self) -> None:
        """F2: an orphaned `yes` kept writing to the (deleted) scratch stdout file after the gate returned."""
        gate, _elapsed, survivors = self.run_one("(setsid env -i yes 41.104 &) ; exit 0", b"41.104")

        self.assertEqual(survivors, [])
        self.assertEqual(gate["result"], "fail", gate)
        self.assertLessEqual(gate["stdout_bytes"], 64 * 1024 * 1024)

    def test_well_behaved_gate_that_waits_for_its_children_passes(self) -> None:
        """Control: a gate whose subprocesses finish before it exits is an ordinary pass."""
        gate, _elapsed, _survivors = self.run_one("sleep 0.2 & wait; echo ok", b"no-such-token")

        self.assertEqual(gate["result"], "pass", gate)
        self.assertEqual(gate.get("left_processes"), 0, gate)
        self.assertIsNone(gate.get("reason"), gate)


class GateDescendantFallbackTests(GateDescendantTests):
    """Same behaviour without namespaces: subreaper + parent-pid walking must do the work."""

    def runner(self, command: str, directory: str, timeout: float) -> list[dict]:
        from agentctl.gate_runner import run_checks

        results = run_checks([command], directory, timeout, hard_limits={"isolation": "none"})
        self.assertEqual(results[0]["gate_isolation"], "none")
        return results


@unittest.skipUnless(LINUX, "/proc access control is Linux-specific")
class GateCannotReachTheIntegratorProcessTests(unittest.TestCase):
    """F1(a): a gate read SECRET_TOKEN from /proc/$PPID/environ and the repository from /proc/$PPID/cwd."""

    def test_gate_cannot_read_the_environment_cwd_or_open_files_of_the_process_running_it(self) -> None:
        with tempfile.TemporaryDirectory(prefix="integrator-cwd-") as runner_cwd, \
                tempfile.TemporaryDirectory() as gate_cwd:
            secret_file = pathlib.Path(runner_cwd) / "held-open.txt"
            secret_file.write_text("x")
            # Only a count of the synthetic sentinel and file paths are written: never raw environment.
            probe = (
                '{ echo "SENTINEL=$(grep -c s3cr3t-in-runner-environ /proc/$PPID/environ)"; '
                'echo "ANY=$(grep -ls s3cr3t-in-runner-environ /proc/[0-9]*/environ | wc -l)"; '
                'for p in /proc/[0-9]*; do readlink "$p/cwd"; done; '
                'echo "CWD=$(readlink /proc/$PPID/cwd)"; '
                'for f in /proc/$PPID/fd/*; do readlink "$f"; done; } > probe.out 2>/dev/null; true'
            )
            script = textwrap.dedent(
                """
                import sys
                from agentctl.integration_queue import run_gate_commands
                held = open(sys.argv[3])
                (gate,) = run_gate_commands([sys.argv[1]], cwd=sys.argv[2], timeout=20)
                print(gate["result"], gate.get("gate_isolation"))
                """
            )
            env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "PYTHONPATH": str(AGENTCTL_DIR),
                "SECRET_TOKEN": "s3cr3t-in-runner-environ",
            }
            done = subprocess.run(
                [sys.executable, "-c", script, probe, gate_cwd, str(secret_file)],
                cwd=runner_cwd, env=env, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            seen = (pathlib.Path(gate_cwd) / "probe.out").read_text()
            if done.stdout.split()[-1:] == ["none"]:
                self.skipTest("no user+PID namespace here; gate_isolation recorded as 'none' (no claim)")

        self.assertNotIn("SENTINEL=1", seen)
        self.assertIn("ANY=0", seen)
        self.assertNotIn(runner_cwd, seen)


@unittest.skipUnless(LINUX, "prctl is Linux-specific")
class IntegratorHardeningTests(unittest.TestCase):
    def test_hardening_marks_the_process_non_dumpable_and_a_subreaper_then_restores_it(self) -> None:
        """F1(a)/F2: the integrator must be non-dumpable and a child subreaper while gates run."""
        from agentctl.gate_runner import integrator_hardening

        libc = ctypes.CDLL(None, use_errno=True)
        reaper = ctypes.c_int(0)

        def state() -> tuple[int, int]:
            libc.prctl(37, ctypes.byref(reaper), 0, 0, 0)  # PR_GET_CHILD_SUBREAPER
            return libc.prctl(3, 0, 0, 0, 0), reaper.value  # PR_GET_DUMPABLE

        before = state()
        with integrator_hardening() as status:
            self.assertEqual(state(), (0, 1))
            self.assertTrue(status["non_dumpable"])
            self.assertTrue(status["child_subreaper"])
        self.assertEqual(state(), before)


class SensitiveEnvironmentTests(unittest.TestCase):
    def test_integrator_drops_credential_variables_and_keeps_the_rest(self) -> None:
        """F5: the integrate command drops ANTHROPIC_*/CLAUDE*/*TOKEN*/*SECRET*/*KEY* from os.environ."""
        from unittest import mock

        from agentctl.gate_runner import drop_sensitive_environment

        synthetic = {
            "ANTHROPIC_API_KEY": "sk-synthetic", "CLAUDE_CODE_X": "synthetic",
            "MY_TOKEN": "synthetic", "DB_SECRET": "synthetic", "KEEP_ME": "kept",
        }
        with mock.patch.dict(os.environ, synthetic, clear=True):
            removed = drop_sensitive_environment()
            remaining = dict(os.environ)

        self.assertEqual(removed, ["ANTHROPIC_API_KEY", "CLAUDE_CODE_X", "DB_SECRET", "MY_TOKEN"])
        self.assertEqual(remaining, {"KEEP_ME": "kept"})


if __name__ == "__main__":
    unittest.main()
