"""The runc sandbox itself: layout, privileges, network, deadline, limits, cleanup, fail-closed probe.

Real ``runc`` containers, real files, real sockets. Planted canaries are harmless files this test creates.
Tests that need ``runc`` are skipped, labelled, when ``sandbox.probe()`` says it is unavailable; the probe's
own never-raises behaviour is tested without runc and is never skipped.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
import socket
import subprocess
import tempfile
import textwrap
import threading
import unittest
from unittest import mock

from agentctl import sandbox

AVAILABLE = sandbox.probe()["available"]
NEEDS_RUNC = unittest.skipUnless(AVAILABLE, "runc sandbox unavailable on this host (sandbox tests only)")
EXPECTED_ROOT = {"bin", "dev", "etc", "lib", "lib64", "proc", "sbin", "tmp", "usr", "work"}
ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8"}


def _host_processes_with(token: bytes) -> list[int]:
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
        self.scratch = pathlib.Path(tempfile.mkdtemp(prefix="sandbox-test-"))
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.tree = self.scratch / "tree"
        self.tree.mkdir()
        (self.tree / "present.txt").write_text("in the tree\n")

    def run_sandboxed(self, command: str, *, timeout: float = 30.0, **kwargs) -> dict:
        kwargs.setdefault("env", ENV)
        return sandbox.run_in_sandbox(
            command, tree_dir=self.tree, timeout=timeout, out_dir=self.scratch / "out", **kwargs
        )

    def output(self, command: str, **kwargs) -> str:
        result = self.run_sandboxed(command, **kwargs)
        self.assertEqual(result["exit_code"], 0, result)
        return result["stdout_excerpt"]


class ProbeNeverRaisesTests(unittest.TestCase):
    """Runs everywhere: the probe reports unavailability, it never raises and never runs anything."""

    def test_a_missing_runc_is_a_structured_unavailable_result(self) -> None:
        with mock.patch.dict(os.environ, {"AGENTCTL_RUNC": "/nonexistent/agentctl-test/runc"}):
            status = sandbox.probe()
            with self.assertRaises(sandbox.SandboxUnavailable) as raised:
                sandbox.run_in_sandbox("true", tree_dir=tempfile.gettempdir(), timeout=5, out_dir=tempfile.gettempdir())
        self.assertIs(status["available"], False)
        self.assertEqual(status["reason"], "runc_not_found")
        self.assertEqual(raised.exception.reason, "runc_not_found")

    def test_a_runc_that_cannot_run_containers_is_unavailable_not_an_exception(self) -> None:
        fake = pathlib.Path(tempfile.mkdtemp(prefix="fake-runc-")) / "runc"
        self.addCleanup(shutil.rmtree, fake.parent, True)
        fake.write_text("#!/bin/sh\necho 'runc version 0.0 (fake)'\nexit 1\n")
        fake.chmod(0o755)
        with mock.patch.dict(os.environ, {"AGENTCTL_RUNC": str(fake)}):
            status = sandbox.probe()
        self.assertIs(status["available"], False)
        self.assertIn(status["reason"], {"runc_cannot_start_container", "sandbox_requires_root"})

    def test_a_runc_that_ignores_the_isolation_is_caught_by_the_canary(self) -> None:
        """SIMULATED broken sandbox: a 'runc' that runs the bundle's command directly on the host."""
        if os.geteuid() != 0:
            self.skipTest("needs root to model the root-run runc case")
        fake = pathlib.Path(tempfile.mkdtemp(prefix="fake-runc-")) / "runc"
        self.addCleanup(shutil.rmtree, fake.parent, True)
        fake.write_text(textwrap.dedent("""\
            #!/usr/bin/env python3
            import json, subprocess, sys, os
            if "--version" in sys.argv:
                print("runc version 0.0 (host passthrough)"); sys.exit(0)
            if "run" not in sys.argv: sys.exit(0)
            bundle = sys.argv[sys.argv.index("--bundle") + 1]
            pid_file = sys.argv[sys.argv.index("--pid-file") + 1]
            config = json.load(open(bundle + "/config.json"))
            open(pid_file, "w").write(str(os.getpid()))
            env = dict(item.split("=", 1) for item in config["process"]["env"])
            sys.exit(subprocess.run(config["process"]["args"], env=env).returncode)
            """))
        fake.chmod(0o755)
        with mock.patch.dict(os.environ, {"AGENTCTL_RUNC": str(fake)}):
            status = sandbox.probe()
        self.assertIs(status["available"], False)
        self.assertEqual(status["reason"], "sandbox_boundary_check_failed", status)


class ProbeIsNotSilentlySkippedTests(unittest.TestCase):
    """The sandbox tests are skipped when runc is absent. They must never be skipped because the boundary
    check FAILED: that is a regression (or a leaking host), not an unavailable tool."""

    def test_a_failed_boundary_check_is_never_mistaken_for_an_unavailable_sandbox(self) -> None:
        status = sandbox.probe()

        self.assertNotEqual(status["reason"], "sandbox_boundary_check_failed", status)
        self.assertNotEqual(status["reason"], "probe_error", status)


@NEEDS_RUNC
class ProbeTests(unittest.TestCase):
    def test_the_probe_proves_the_boundary_and_reports_structured_facts(self) -> None:
        status = sandbox.probe(refresh=True)

        self.assertIs(status["available"], True, status)
        self.assertEqual(status["reason"], "ok")
        self.assertRegex(status["runtime_hash"], r"^[0-9a-f]{64}$")
        enforced = status["limits_enforced"]
        self.assertEqual(set(enforced), {"rlimits", "seccomp", "capabilities", "memory", "pids", "cpu"})
        self.assertIs(enforced["rlimits"], True)
        self.assertEqual(sandbox.probe(), status)  # cached: same facts, no new container needed


@NEEDS_RUNC
class LayoutAndPrivilegeTests(_Case):
    def test_the_result_is_structured_and_the_command_runs_as_a_non_root_user_in_work(self) -> None:
        result = self.run_sandboxed("echo hello; echo oops >&2; id -u; pwd; exit 7")

        self.assertEqual(result["exit_code"], 7)
        self.assertIs(result["timed_out"], False)
        self.assertEqual(result["isolation"], "runc")
        self.assertEqual(result["network"], "none")
        self.assertEqual(result["stdout_excerpt"], "hello\n65534\n/work\n")
        self.assertEqual(result["stdout_hash"], hashlib.sha256(b"hello\n65534\n/work\n").hexdigest())
        self.assertEqual(result["stderr_hash"], hashlib.sha256(b"oops\n").hexdigest())
        self.assertEqual((result["stdout_bytes"], result["stderr_bytes"]), (18, 5))
        self.assertIsInstance(result["duration_ms"], int)
        self.assertTrue(result["container_deleted"] and result["bundle_removed"])
        self.assertIn("limits_enforced", result)
        # Output is in files opened by the parent, not carried over pipes.
        self.assertEqual(len(list((self.scratch / "out").glob("*.stdout"))), 1)

    def test_only_the_runtime_subset_the_tree_tmp_and_proc_exist(self) -> None:
        entries = set(self.output("ls -A /").split())

        self.assertEqual(entries - EXPECTED_ROOT, set(), entries)
        for absent in ("/root", "/home", "/var", "/run", "/mnt", "/opt", "/srv", "/var/run/docker.sock"):
            result = self.run_sandboxed(f"test ! -e {absent}")
            self.assertEqual(result["exit_code"], 0, absent)
        destinations = {m["destination"] for m in self.run_sandboxed("true")["mounts"]}
        self.assertEqual(destinations - {"/proc", "/dev", "/dev/shm", "/usr", "/lib", "/lib64", "/bin", "/sbin",
                                         "/etc/alternatives", "/work", "/tmp"}, set())
        work = [m for m in self.run_sandboxed("true")["mounts"] if m["destination"] == "/work"]
        self.assertEqual([m["kind"] for m in work], ["bind-rw"])

    def test_the_runtime_is_read_only_while_work_and_tmp_are_writable(self) -> None:
        text = self.output(
            "for p in /usr/x /bin/x /lib/x /etc/x /x; do (: > $p) 2>/dev/null && echo WRITABLE:$p; done; "
            ": > /work/created && echo work-ok; : > /tmp/created && echo tmp-ok; echo done"
        )

        self.assertEqual(text.split(), ["work-ok", "tmp-ok", "done"])
        self.assertTrue((self.tree / "created").exists())  # the tree is the writable, persistent part
        self.assertEqual(os.lstat(self.tree / "present.txt").st_uid, sandbox.SANDBOX_UID)

    def test_no_capabilities_no_new_privileges_seccomp_and_no_user_namespace(self) -> None:
        text = self.output(
            "grep -E '^(CapEff|CapBnd|CapPrm|CapAmb|NoNewPrivs|Seccomp):' /proc/self/status; "
            "unshare --user true 2>/dev/null && echo USERNS-ALLOWED; unshare --mount true 2>/dev/null && echo MNT-ALLOWED; "
            "echo end"
        )

        fields = dict(line.split(":\t") for line in text.splitlines() if ":\t" in line)
        for name in ("CapEff", "CapBnd", "CapPrm", "CapAmb"):
            self.assertEqual(fields[name].strip(), "0000000000000000", name)
        self.assertEqual(fields["NoNewPrivs"].strip(), "1")
        self.assertEqual(fields["Seccomp"].strip(), "2")  # filter mode
        self.assertNotIn("ALLOWED", text)

    def test_ordinary_tooling_works_inside_python_git_threads_subprocesses(self) -> None:
        text = self.output(
            "python3 -c \"import threading, subprocess, sys; "
            "t = threading.Thread(target=lambda: None); t.start(); t.join(); "
            "print(subprocess.run(['git', '--version'], capture_output=True, text=True).stdout.split()[0])\"; "
            "git init -q . && git add -A && git -c user.name=a -c user.email=a@a.invalid commit -qm x && "
            "git ls-files; awk 'BEGIN { print 6 * 7 }'"
        )

        self.assertEqual(text.split(), ["git", "present.txt", "42"])

    def test_the_tree_is_a_bind_of_the_given_directory_symlinks_do_not_escape(self) -> None:
        canary = self.scratch / "host-only-canary.secret"
        canary.write_text("synthetic\n")
        os.symlink(canary, self.tree / "link-to-host-canary")
        os.symlink("../../../../../../etc/shadow", self.tree / "link-to-shadow")

        result = self.run_sandboxed("cat link-to-host-canary link-to-shadow 2>&1; echo rc=$?")

        self.assertNotIn("synthetic", result["stdout_excerpt"])
        self.assertIn("rc=1", result["stdout_excerpt"])


@NEEDS_RUNC
class HostIsolationTests(_Case):
    def test_a_planted_host_file_cannot_be_read_and_the_store_file_is_byte_identical_after_hostile_commands(self) -> None:
        store = self.scratch / "agentctl.sqlite3"  # stands for the control-plane event store
        store.write_bytes(b"SQLite format 3\0" + os.urandom(512))
        canary = self.scratch / "credentials.canary"
        canary.write_text("SYNTHETIC-NOT-A-SECRET\n")
        before = hashlib.sha256(store.read_bytes()).hexdigest()

        text = self.output(
            f"cat {canary} 2>&1; rm -f {store}; echo x >> {store}; rm -rf {self.scratch} 2>&1; "
            f"ls {self.scratch} 2>&1; echo end"
        )

        self.assertNotIn("SYNTHETIC", text)
        self.assertEqual(hashlib.sha256(store.read_bytes()).hexdigest(), before)
        self.assertEqual(canary.read_text(), "SYNTHETIC-NOT-A-SECRET\n")

    def test_no_network_request_reaches_a_host_listener_or_anything_else(self) -> None:
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(4)
        self.addCleanup(server.close)
        accepted: list[int] = []

        def serve() -> None:
            while True:
                try:
                    server.accept()[0].close()
                except OSError:
                    return
                accepted.append(1)

        threading.Thread(target=serve, daemon=True).start()
        port = server.getsockname()[1]

        text = self.output(
            "python3 - <<'PY'\n"
            "import socket\n"
            f"for target in (('127.0.0.1', {port}), ('192.0.2.1', 80), ('203.0.113.7', 443), ('::1', {port})):\n"
            "    try:\n"
            "        socket.create_connection(target, timeout=1.5).close(); print('CONNECTED', target)\n"
            "    except OSError as error:\n"
            "        print('blocked', target[0], type(error).__name__)\n"
            "PY\n"
            "cat /proc/net/dev | sed -n '3,$p' | cut -d: -f1 | tr -d ' '"
        )

        self.assertNotIn("CONNECTED", text)
        self.assertEqual(text.split()[-1], "lo")  # the only interface
        self.assertEqual(accepted, [])


@NEEDS_RUNC
class DeadlineLimitsAndCleanupTests(_Case):
    def test_a_timeout_kills_a_double_forked_setsid_straggler_and_deletes_the_container(self) -> None:
        token = "42.4242"
        self.addCleanup(lambda: [os.kill(pid, 9) for pid in _host_processes_with(token.encode())
                                 if os.path.exists(f"/proc/{pid}")])
        command = f"setsid sh -c '(sleep {token} &) ; sleep {token}' & setsid sleep {token} & sleep 60"

        result = self.run_sandboxed(command, timeout=2.0)

        self.assertIs(result["timed_out"], True)
        self.assertEqual(result["exit_code"], 124)
        self.assertLess(result["duration_ms"], 20000)
        self.assertEqual(_host_processes_with(token.encode()), [])
        listing = subprocess.run(
            [sandbox.runc_binary(), "--root", sandbox.state_root(), "list", "-q"], capture_output=True, text=True
        )
        self.assertEqual(listing.stdout.split(), [])
        self.assertTrue(result["container_deleted"] and result["bundle_removed"])

    def test_every_run_has_a_fresh_container_id_and_leaves_no_bundle_behind(self) -> None:
        before = {p for p in pathlib.Path(tempfile.gettempdir()).glob("agentctl-sandbox-*")}

        first, second = self.run_sandboxed("true"), self.run_sandboxed("true")

        self.assertNotEqual(first["container_id"], second["container_id"])
        after = {p for p in pathlib.Path(tempfile.gettempdir()).glob("agentctl-sandbox-*")}
        self.assertEqual(after - before, set())

    def test_output_is_bounded_by_the_file_size_limit(self) -> None:
        result = self.run_sandboxed("yes AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA; echo unreachable", limits={"file_size_bytes": 1 << 20})

        self.assertIs(result["output_limit_exceeded"], True)
        self.assertLessEqual(result["stdout_bytes"], 1 << 20)
        self.assertNotEqual(result["exit_code"], 0)

    def test_memory_limit_is_enforced_when_the_host_accepts_it_and_reported_when_it_does_not(self) -> None:
        enforced = sandbox.probe()["limits_enforced"]["memory"]
        result = self.run_sandboxed(
            "python3 -c 'x = bytearray(900 * 1024 * 1024); print(len(x))'", limits={"memory_bytes": 128 * 1024 * 1024}
        )

        self.assertIs(result["limits_enforced"]["memory"], enforced)
        if enforced:
            self.assertNotEqual(result["exit_code"], 0, result)  # killed by the memory cgroup
            self.assertNotIn("943718400", result["stdout_excerpt"])
        else:
            self.skipTest("this host does not accept a memory cgroup limit; limits_enforced.memory is False")

    def test_a_container_that_cannot_start_is_reported_as_not_started_and_leaves_nothing_behind(self) -> None:
        """SIMULATED infrastructure fault: a mount whose source does not exist makes runc refuse to start."""
        real = sandbox.build_config

        def broken(*args, **kwargs):
            config = real(*args, **kwargs)
            config["mounts"].append(
                {"destination": "/bad", "type": "bind", "source": "/nonexistent/agentctl-test", "options": ["rbind", "ro"]}
            )
            return config

        before = set(pathlib.Path(tempfile.gettempdir()).glob("agentctl-sandbox-*"))
        with mock.patch.object(sandbox, "build_config", broken):
            result = self.run_sandboxed("echo must-not-run")

        self.assertIs(result["started"], False)
        self.assertNotIn("must-not-run", result["stdout_excerpt"])
        self.assertTrue(result["runtime_error"])
        self.assertEqual(set(pathlib.Path(tempfile.gettempdir()).glob("agentctl-sandbox-*")) - before, set())

    def test_an_internal_error_while_building_the_bundle_leaves_no_bundle_or_container(self) -> None:
        before = set(pathlib.Path(tempfile.gettempdir()).glob("agentctl-sandbox-*"))
        with mock.patch.object(sandbox, "build_config", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.run_sandboxed("true")
        self.assertEqual(set(pathlib.Path(tempfile.gettempdir()).glob("agentctl-sandbox-*")) - before, set())
        listing = subprocess.run(
            [sandbox.runc_binary(), "--root", sandbox.state_root(), "list", "-q"], capture_output=True, text=True
        )
        self.assertEqual(listing.stdout.split(), [])


if __name__ == "__main__":
    unittest.main()
