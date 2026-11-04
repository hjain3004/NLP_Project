"""Regression tests for the independent review's two blocking findings.

1. A gate must not be able to reach, or poison, the repository the integrator keeps using.
2. A gate timeout must be a hard deadline that no descendant process can extend.

Each test failed against the implementation at commit 9d51a9e (before-fix evidence is in
reports/lane-b-2.md) and passes after the repair.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from agentctl import integrate as integrate_module
from agentctl.integrate import _Git, _integration_dir, _materialize_tree, integrate
from agentctl.integration_queue import IntegrationRefused, IntegrationRejected, run_gate_commands

from tests.helpers_lane_b import BRANCH, Lab


class GateContainmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lab = Lab()
        self.addCleanup(self.lab.close)
        self.scratch = pathlib.Path(self.lab._tmp.name)

    def _planter(self, log: pathlib.Path) -> str:
        return f"/bin/sh -c 'echo RAN-with-SECRET=$SECRET_TOKEN >> {log}' --"

    def test_gate_has_no_git_link_and_cannot_change_repository_configuration(self) -> None:
        """Blocker 1 (containment): gates run in a tree with no .git link, outside the checkout."""
        probe = self.scratch / "probe.log"
        config = self.lab.root / ".git" / "config"
        before = config.read_bytes()
        # Only the integrator's gate HOME triggers the probe: the candidate's own
        # scheduler.verify_candidate run (Lane A's) legitimately executes inside its worktree.
        gate = (
            'case "$HOME" in *agentctl-gate-home*) '
            f'if [ -e .git ] || git rev-parse --git-dir >/dev/null 2>&1; then echo LINKED >> {probe}; fi; '
            f'case "$PWD" in {self.lab.root}/*) echo INSIDE-CHECKOUT >> {probe};; esac; '
            'git config diff.external "/bin/sh -c true --" 2>/dev/null;; esac; true'
        )
        self.lab.candidate("T-A", {"docs/a.txt": "a\n"}, checks=(gate,))

        result = integrate("T-A", store=self.lab.store)

        self.assertEqual(result["outcome"], "integrated", result)
        self.assertFalse(probe.exists(), probe.read_text() if probe.exists() else "")
        self.assertEqual(config.read_bytes(), before)
        self.assertEqual(self.lab.git("config", "--local", "--get", "diff.external", check=False), "")

    def test_planted_git_configuration_cannot_run_code_in_later_integrator_runs(self) -> None:
        """Blocker 1 (defence in depth): even a gate that knows the repository path cannot make the
        integrator's own git run planted commands (diff.external, core.fsmonitor)."""
        log = self.scratch / "plant.log"
        root = self.lab.root
        plant = (
            f'case "$HOME" in *agentctl-gate-home*) '
            f'git -C {root} config diff.external "{self._planter(log)}"; '
            f'git -C {root} config core.fsmonitor "{self._planter(log)}";; esac; true'
        )
        self.lab.candidate("T-A", {"docs/a.txt": "a\n"}, checks=(plant,))
        self.lab.candidate("T-B", {"docs/b.txt": "b\n"})
        env_secret = {"SECRET_TOKEN": "s3cr3t"}

        with mock.patch.dict(os.environ, env_secret):
            first = integrate("T-A", store=self.lab.store)
            second = integrate("T-B", store=self.lab.store)

        self.assertEqual(first["outcome"], "integrated", first)
        self.assertEqual(second["outcome"], "integrated", second)  # not candidate_binding_mismatch
        self.assertFalse(log.exists(), log.read_text() if log.exists() else "")
        self.assertEqual(self.lab.tree_file("docs/b.txt"), "b\n")


class GateDeadlineTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("setsid"), "needs util-linux setsid")
    def test_descendant_that_escapes_the_process_group_cannot_extend_the_timeout(self) -> None:
        """Blocker 2: `setsid sleep 15 & exit 0` returned after 15 s because the pipe stayed open."""
        with tempfile.TemporaryDirectory() as directory:
            started = time.monotonic()
            (gate,) = run_gate_commands(["setsid sleep 15 & exit 0"], cwd=directory, timeout=1.0)
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 5.0, f"gate deadline not enforced: {elapsed:.1f}s")
        self.assertTrue(gate["timed_out"], gate)
        self.assertEqual(gate["result"], "fail")


class GateTreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lab = Lab()
        self.addCleanup(self.lab.close)

    def test_gate_sees_the_exact_planned_tree_even_for_export_ignored_files(self) -> None:
        """Protects gate fidelity: the gate tree is the reviewed tree, not what `git archive`
        (which honours the candidate's export-ignore attributes) would produce."""
        self.lab.candidate(
            "T1",
            {".gitattributes": "src/hidden.txt export-ignore\n", "src/hidden.txt": "present\n"},
            checks=("test -f src/hidden.txt && test -f .gitattributes",),
        )

        result = integrate("T1", store=self.lab.store)

        self.assertEqual(result["outcome"], "integrated", result)

    def test_crafted_tree_with_a_dot_git_entry_is_refused_not_written(self) -> None:
        """Protects the integrator: a tree that smuggles `.git/config` is never materialized."""
        blob = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"], cwd=self.lab.root, input="x\n",
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        inner = subprocess.run(
            ["git", "mktree"], cwd=self.lab.root, input=f"100644 blob {blob}\tconfig\n",
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        outer = subprocess.run(
            ["git", "mktree"], cwd=self.lab.root, input=f"040000 tree {inner}\t.git\n",
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        commit = self.lab.git("commit-tree", outer, "-m", "crafted")
        with tempfile.TemporaryDirectory() as directory:
            destination = pathlib.Path(directory) / "tree"

            with self.assertRaises(IntegrationRejected) as raised:
                _materialize_tree(_Git(self.lab.root), commit, destination)

            self.assertEqual(raised.exception.reason_code, "candidate_tree_unsafe")
            self.assertFalse((destination / ".git").exists())


class TaskIdPathTests(unittest.TestCase):
    def test_identifiers_that_would_escape_the_integration_directory_are_refused(self) -> None:
        """Protects the filesystem: an id becomes a path component only if it matches the task-id pattern."""
        root = pathlib.Path("/nonexistent-root")
        for bad in ("../escape", "a/b", "", ".hidden", "x y", "a..b"):
            with self.subTest(task_id=bad), self.assertRaises(IntegrationRefused):
                _integration_dir(root, bad)
        self.assertEqual(
            _integration_dir(root, "TASK-1.a_b"),
            root / "agent-system/runtime/integration/TASK-1.a_b",
        )


class MergeAttributeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lab = Lab()
        self.addCleanup(self.lab.close)

    def test_union_merge_attribute_cannot_silently_resolve_a_real_conflict(self) -> None:
        """Protects the no-resolution rule: `merge=union` would combine two edits of one file cleanly,
        but the integrator must still reject a path both sides changed."""
        self.lab.advance_base(
            {".gitattributes": "docs/union.txt merge=union\n", "docs/union.txt": "line1\n"}
        )
        first = self.lab.candidate("T1", {"docs/union.txt": "line1\nfrom-T1\n"})
        second = self.lab.candidate("T2", {"docs/union.txt": "line1\nfrom-T2\n"})
        self.assertEqual(integrate("T1", store=self.lab.store)["outcome"], "integrated")
        head = self.lab.branch_head()
        # Precondition: plain git really would merge these two cleanly and drop the conflict.
        probe = pathlib.Path(self.lab._tmp.name) / "probe-worktree"
        self.lab.git("worktree", "add", "--quiet", "--detach", str(probe), first.result_commit)
        try:
            plain = subprocess.run(
                ["git", "-c", "user.name=p", "-c", "user.email=p@x.invalid", "merge", "--no-edit",
                 second.result_commit], cwd=probe, capture_output=True, text=True,
            )
            self.assertEqual(plain.returncode, 0, plain.stdout + plain.stderr)
            self.assertIn("from-T1", (probe / "docs/union.txt").read_text())
            self.assertIn("from-T2", (probe / "docs/union.txt").read_text())
        finally:
            self.lab.git("worktree", "remove", "--force", str(probe))

        result = integrate("T2", store=self.lab.store)

        self.assertEqual(result["outcome"], "rejected", result)
        self.assertEqual(result["reason_code"], "conflict_repair_required")
        self.assertEqual(result["details"]["conflicted_paths"], ["docs/union.txt"])
        self.assertEqual(self.lab.branch_head(), head)
        self.assertNotEqual(self.lab.state("T2"), "completed")


class CandidateAlteredTests(unittest.TestCase):
    def test_simulated_merge_that_changes_candidate_content_is_refused(self) -> None:
        """SIMULATED merge-time tampering (a driver or hook rewriting the merge commit). Protects the
        'preserve the reviewed patch' invariant: the planned result must carry the candidate's paths exactly."""
        lab = Lab()
        self.addCleanup(lab.close)
        lab.candidate("T1", {"src/alpha.txt": "alpha\n"})
        lab.candidate("T2", {"src/beta.txt": "beta\n"})
        self.assertEqual(integrate("T1", store=lab.store)["outcome"], "integrated")

        class TamperingGit(_Git):
            def run(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                done = super().run(*args, **kwargs)
                if args[:1] == ("merge",) and done.returncode == 0:
                    worktree = pathlib.Path(kwargs["cwd"])
                    (worktree / "src/beta.txt").write_text("TAMPERED\n")
                    super().run("commit", "--amend", "--no-edit", "-a", cwd=worktree,
                                env=kwargs.get("env"))
                return done

        git = TamperingGit(lab.root)
        evidence = integrate_module._verify(lab.store, git, "T2", "an-independent-integrator")
        worktree = lab.root / "agent-system/runtime/integration/T2/wt-altered"
        try:
            with self.assertRaises(IntegrationRejected) as raised:
                integrate_module._plan(
                    git, evidence, old_head=lab.branch_head(), planned_at=1_700_000_000,
                    integration_id="T2-I9", branch=BRANCH, worktree=worktree, check_timeout=5.0,
                )
        finally:
            git.remove_worktree(worktree)

        self.assertEqual(raised.exception.reason_code, "candidate_altered")


if __name__ == "__main__":
    unittest.main()
