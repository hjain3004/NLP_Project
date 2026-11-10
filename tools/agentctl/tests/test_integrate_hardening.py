"""Regression tests for the independent reviews' findings on gate containment.

1. A gate must not be able to reach, or poison, the repository the integrator keeps using.
2. A gate timeout must be a hard deadline that no descendant process can extend.

Classes up to MergeAttributeTests failed against 9d51a9e (reports/lane-b-2.md). The
second review's F1 (GateCannotReachIntegratorTests, GateRepositoryTamperTests) and F4
(GateTreeTests symlink/duplicate cases) failed against dbba2c2 (reports/lane-b-3.md).
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from agentctl import integrate as integrate_module
from agentctl.integrate import _Git, _integration_dir, _materialize_tree, integrate
from agentctl.integration_queue import IntegrationRefused, IntegrationRejected, run_gate_commands

from tests.helpers_lane_b import BRANCH, Lab


def use_legacy_runner(case: unittest.TestCase) -> None:
    """Select the explicit ``legacy-unsafe`` check runner for the rest of ``case``.

    These classes assert the legacy runner's own defences (no ``.git`` link in the integration copy,
    repository tamper hashes, the namespace that hides the integrator's /proc). Since the runc sandbox
    became the default, that runner is reached only by this explicit selection; the sandbox boundary is
    tested in ``test_check_environment.py`` and ``test_sandbox.py``.
    """
    patcher = mock.patch.dict(os.environ, {"AGENTCTL_CHECK_ISOLATION": "legacy-unsafe"})
    patcher.start()
    case.addCleanup(patcher.stop)


class GateContainmentTests(unittest.TestCase):
    def setUp(self) -> None:
        use_legacy_runner(self)
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
        # Only the integration copy triggers the probe. Verification now runs through the same
        # gate runner (same HOME) but in the leased worktree, which legitimately has a `.git`
        # link file, so the guard is the working directory, not HOME. The integration copy is
        # created under an `agentctl-integration-gate-*` temporary directory with no `.git`.
        ran = self.scratch / "ran-in-copy.log"
        gate = (
            'case "$PWD" in *agentctl-integration-gate-*) '
            f'echo COPY >> {ran}; '  # positive control: the probe below really ran in the copy
            f'if [ -e .git ] || git rev-parse --git-dir >/dev/null 2>&1; then echo LINKED >> {probe}; fi; '
            f'case "$PWD" in {self.lab.root}/*) echo INSIDE-CHECKOUT >> {probe};; esac; '
            'git config diff.external "/bin/sh -c true --" 2>/dev/null;; esac; true'
        )
        self.lab.candidate("T-A", {"docs/a.txt": "a\n"}, checks=(gate,))

        result = integrate("T-A", store=self.lab.store)

        self.assertEqual(result["outcome"], "integrated", result)
        self.assertEqual(ran.read_text().split() if ran.exists() else [], ["COPY"])  # exactly once
        self.assertFalse(probe.exists(), probe.read_text() if probe.exists() else "")
        self.assertEqual(config.read_bytes(), before)
        self.assertEqual(self.lab.git("config", "--local", "--get", "diff.external", check=False), "")

    def test_planted_git_configuration_cannot_run_code_in_later_integrator_runs(self) -> None:
        """Blocker 1 (defence in depth): configuration planted by another process that shares `.git`
        (for example the candidate's own verification run) cannot make the integrator's git run
        planted commands (diff.external, core.fsmonitor). A *gate* that plants configuration is
        rejected outright: see GateRepositoryTamperTests."""
        log = self.scratch / "plant.log"
        self.lab.candidate("T-B", {"docs/b.txt": "b\n"})
        self.lab.git("config", "diff.external", self._planter(log))
        self.lab.git("config", "core.fsmonitor", self._planter(log))

        with mock.patch.dict(os.environ, {"SECRET_TOKEN": "s3cr3t"}):
            second = integrate("T-B", store=self.lab.store)

        self.assertEqual(second["outcome"], "integrated", second)  # not candidate_binding_mismatch
        self.assertFalse(log.exists(), log.read_text() if log.exists() else "")
        self.assertEqual(self.lab.tree_file("docs/b.txt"), "b\n")


def _plant_filter_driver(git_dir: str, log: pathlib.Path) -> str:
    """Shell that plants a smudge filter for every path, as the reviewer's PoC did."""
    return (
        f'git config --file "{git_dir}/config" filter.x.smudge '
        f'"sh -c \'echo RAN >> {log}; cat\'"; '
        f'git config --file "{git_dir}/config" filter.x.clean "sh -c \'echo RAN >> {log}; cat\'"; '
        f'git config --file "{git_dir}/config" filter.x.required false; '
        f'mkdir -p "{git_dir}/info"; echo "* filter=x" >> "{git_dir}/info/attributes"'
    )


class GateCannotReachIntegratorTests(unittest.TestCase):
    """F1(a), end to end through the real `agentctl integrate` process."""

    def setUp(self) -> None:
        use_legacy_runner(self)
        self.lab = Lab()
        self.addCleanup(self.lab.close)
        self.scratch = pathlib.Path(self.lab._tmp.name)

    @unittest.skipUnless(sys.platform.startswith("linux"), "/proc is Linux-specific")
    def test_gate_cannot_read_the_integrator_environment_or_repository_path_through_proc(self) -> None:
        """The reviewer's gate read SECRET_TOKEN from /proc/$PPID/environ and the repository root
        from /proc/$PPID/cwd, then used that path to plant a filter driver."""
        leak = self.scratch / "leak.log"
        gate = (
            # Log only from the integration copy; verification (leased worktree) runs the same
            # command through the same runner and must not be what this test measures.
            'case "$PWD" in *agentctl-integration-gate-*) '
            '{ echo "SENTINEL=$(grep -c s3cr3t-proc /proc/$PPID/environ)"; '
            'echo "ANY=$(grep -ls s3cr3t-proc /proc/[0-9]*/environ | wc -l)"; '
            'for p in /proc/[0-9]*; do readlink "$p/cwd"; done; '
            'echo "CWD=$(readlink /proc/$PPID/cwd)"; '
            'for f in /proc/$PPID/fd/*; do readlink "$f"; done; } '
            f'>> {leak} 2>/dev/null;; esac; true'
        )
        self.lab.candidate("T-A", {"docs/a.txt": "a\n"}, checks=(gate,))

        with mock.patch.dict(os.environ, {"SECRET_TOKEN": "s3cr3t-proc"}):
            run = self.lab.cli("T-A")

        self.assertEqual(run.code, 0, run.stdout + run.stderr)
        seen = leak.read_text() if leak.exists() else ""
        self.assertNotIn("SENTINEL=1", seen)
        self.assertIn("ANY=0", seen)
        self.assertNotIn(str(self.lab.root), seen)


class GateRepositoryTamperTests(unittest.TestCase):
    """F1(b)/(c): a gate that changes repository metadata is rejected; planted drivers never run."""

    def setUp(self) -> None:
        use_legacy_runner(self)
        self.lab = Lab()
        self.addCleanup(self.lab.close)
        self.scratch = pathlib.Path(self.lab._tmp.name)
        self.git_dir = str(self.lab.root / ".git")
        self.log = self.scratch / "smudge.log"

    def gate(self, body: str) -> str:
        """Run ``body`` only inside the integration copy (no `.git`, `agentctl-integration-gate-*`).

        Verification executes the same contract check in the leased worktree first; tampering
        there is Lane A's behaviour and is asserted separately by
        ``test_tampering_at_verification_time_never_reaches_integration``.
        """
        return f'case "$PWD" in *agentctl-integration-gate-*) {body};; esac; true'

    def assert_not_integrated(self, task_id: str) -> None:
        self.assertIsNone(self.lab.branch_head())
        self.assertEqual(self.lab.state(task_id), "integration_ready")
        self.assertEqual(self.lab.artifacts("integration-record", task_id), [])

    def test_gate_that_plants_a_filter_driver_is_rejected_and_the_driver_never_runs(self) -> None:
        """The reviewer's PoC: the next integration's checkout ran the planted smudge 253 times and
        T-B still reported integrated."""
        self.lab.candidate(
            "T-A", {"docs/a.txt": "a\n"}, checks=(self.gate(_plant_filter_driver(self.git_dir, self.log)),)
        )
        self.lab.candidate("T-B", {"docs/b.txt": "b\n"})

        first = self.lab.cli("T-A")

        self.assertEqual((first.code, first.json["outcome"]), (1, "rejected"), first.stdout)
        self.assertEqual(first.json["reason_code"], "gate_tampered_repository")
        self.assertIn("config", first.json["details"]["changed"])
        self.assert_not_integrated("T-A")
        # Nothing is restored silently: the planted configuration is still there for a human.
        self.assertIn("RAN", self.lab.git("config", "--get", "filter.x.smudge"))

        second = self.lab.cli("T-B")

        self.assertEqual((second.code, second.json["outcome"]), (1, "refused"), second.stdout)
        self.assertEqual(second.json["reason_code"], "gate_tampered_repository")
        self.assert_not_integrated("T-B")
        self.assertFalse(self.log.exists(), self.log.read_text() if self.log.exists() else "")

        # A human inspects and removes what the gate planted; integration resumes.
        self.lab.git("config", "--remove-section", "filter.x")
        (self.lab.root / ".git" / "info" / "attributes").unlink()
        third = self.lab.cli("T-B")

        self.assertEqual(third.json["outcome"], "integrated", third.stdout)
        self.assertEqual(self.lab.tree_file("docs/b.txt"), "b\n")
        self.assertFalse(self.log.exists())

    def test_gate_that_plants_a_hook_is_rejected(self) -> None:
        hook = f"{self.git_dir}/hooks/post-checkout"
        self.lab.candidate(
            "T-A", {"docs/a.txt": "a\n"},
            checks=(self.gate(f'printf "#!/bin/sh\\necho RAN >> {self.log}\\n" > {hook}; chmod +x {hook}'),),
        )

        result = integrate("T-A", store=self.lab.store)

        self.assertEqual(result.get("reason_code"), "gate_tampered_repository", result)
        self.assertIn("hooks/post-checkout", result["details"]["changed"])
        self.assert_not_integrated("T-A")

    def test_gate_that_creates_a_replace_ref_is_rejected(self) -> None:
        """refs/replace/* would make git substitute objects; a gate creating one is tampering."""
        root = self.lab.root
        self.lab.candidate(
            "T-A", {"docs/a.txt": "a\n"},
            checks=(self.gate(
                f'git -C {root} replace -f $(git -C {root} rev-parse HEAD) $(git -C {root} rev-parse HEAD~1)'
            ),),
        )

        result = integrate("T-A", store=self.lab.store)

        self.assertEqual(result.get("reason_code"), "gate_tampered_repository", result)
        self.assertTrue(any(name.startswith("refs:") for name in result["details"]["changed"]), result)
        self.assert_not_integrated("T-A")

    def test_tampering_at_verification_time_never_reaches_integration(self) -> None:
        """Through the public flow: the same planting command run at VERIFICATION (in the leased
        worktree, which has a `.git` link file) fails verification with gate_tampered_repository,
        so the candidate never becomes integration_ready and `integrate` refuses it. The tamper
        audit itself is Lane A's (scheduler.verify_candidate); this asserts the end-to-end effect."""
        hook = f"{self.git_dir}/hooks/post-checkout"
        plant = f'if [ -e .git ]; then printf "#!/bin/sh\\n" > {hook}; chmod +x {hook}; fi; true'
        candidate = self.lab.candidate("T-A", {"docs/a.txt": "a\n"}, checks=(plant,), review=False)

        self.assertEqual(self.lab.state("T-A"), "repair_required")
        evidence = json.loads(self.lab.store.scalar(
            "SELECT payload_json FROM artifacts WHERE artifact_id = ?",
            (f"verification:T-A:{candidate.attempt_id}",),
        ))
        self.assertIs(evidence["passed"], False)
        self.assertEqual(evidence["repository_tamper"]["reason_code"], "gate_tampered_repository")
        self.assertIn("hooks/post-checkout", evidence["repository_tamper"]["changed"])
        os.unlink(hook)  # a human removes what the check planted

        result = integrate("T-A", store=self.lab.store)

        self.assertEqual(result["outcome"], "refused", result)
        self.assertEqual(result["reason_code"], "task_not_integration_ready")
        self.assertIsNone(self.lab.branch_head())
        self.assertEqual(self.lab.artifacts("integration-record", "T-A"), [])

    def test_filter_driver_present_before_the_run_is_neutralised_and_never_executed(self) -> None:
        """Planted by a process the integrator does not control (e.g. the candidate's own verification
        in its worktree); every integrator git call overrides each configured driver."""
        self.lab.candidate("T-B", {"docs/b.txt": "b\n"})
        subprocess.run(["/bin/sh", "-c", _plant_filter_driver(self.git_dir, self.log)], check=True)
        self.assertIn("RAN", self.lab.git("config", "--get", "filter.x.smudge"))

        run = self.lab.cli("T-B")

        self.assertEqual(run.json["outcome"], "integrated", run.stdout + run.stderr)
        self.assertFalse(self.log.exists(), self.log.read_text() if self.log.exists() else "")
        self.assertEqual(self.lab.tree_file("docs/b.txt"), "b\n")

    def test_driver_configured_after_integrator_startup_refuses_the_integration(self) -> None:
        """A driver key that was not present when the integrator started is refused, not ignored."""
        self.lab.candidate("T-B", {"docs/b.txt": "b\n"})
        git = _Git(self.lab.root)  # startup: no driver configured
        evidence = integrate_module._verify(self.lab.store, git, "T-B", "an-independent-integrator")
        subprocess.run(["/bin/sh", "-c", _plant_filter_driver(self.git_dir, self.log)], check=True)
        worktree = _integration_dir(self.lab.root, "T-B") / "wt-late-driver"
        try:
            with self.assertRaises(IntegrationRefused) as raised:
                integrate_module._plan(
                    git, evidence, old_head=self.lab.base, planned_at=1_700_000_000,
                    integration_id="T-B-I9", branch=BRANCH, worktree=worktree, check_timeout=5.0,
                )
        finally:
            git.remove_worktree(worktree)

        self.assertEqual(raised.exception.reason_code, "gate_tampered_repository")
        self.assertFalse(self.log.exists(), self.log.read_text() if self.log.exists() else "")


class GateDeadlineTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("setsid"), "needs util-linux setsid")
    def test_descendant_that_escapes_the_process_group_cannot_extend_the_timeout(self) -> None:
        """Blocker 2: `setsid sleep 15 & exit 0` returned after 15 s because the pipe stayed open."""
        with tempfile.TemporaryDirectory() as directory:
            started = time.monotonic()
            (gate,) = run_gate_commands(["setsid sleep 15 & exit 0"], cwd=directory, timeout=1.0)
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 5.0, f"gate deadline not enforced: {elapsed:.1f}s")
        # Since lane-b-3 the escaped descendant fails the gate as soon as the shell exits
        # (gate_left_processes) instead of at the deadline; the deadline for a hanging shell
        # is covered by tests.test_gate_runner (descendants_of_a_timed_out_gate...).
        self.assertEqual(gate["result"], "fail")
        self.assertEqual(gate["reason"], "gate_left_processes", gate)


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

    def _crafted_commit(self, tree_lines: list[str]) -> str:
        tree = subprocess.run(
            ["git", "mktree"], cwd=self.lab.root, input="".join(tree_lines),
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return self.lab.git("commit-tree", tree, "-m", "crafted")

    def _blob(self, content: str) -> str:
        return subprocess.run(
            ["git", "hash-object", "-w", "--stdin"], cwd=self.lab.root, input=content,
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def test_symlinked_directory_entry_cannot_create_directories_outside_the_gate_tree(self) -> None:
        """F4: a tree with `d -> OUT` and `d/deep/x` created OUT/deep before the containment check."""
        with tempfile.TemporaryDirectory() as directory:
            outside = pathlib.Path(directory) / "OUT"
            outside.mkdir()
            inner = self._crafted_commit([f"100644 blob {self._blob('x')}\tx\n"])
            inner_tree = self.lab.git("rev-parse", f"{inner}^{{tree}}")
            deep = self._crafted_commit([f"040000 tree {inner_tree}\tdeep\n"])
            deep_tree = self.lab.git("rev-parse", f"{deep}^{{tree}}")
            commit = self._crafted_commit([
                f"120000 blob {self._blob(str(outside))}\td\n",
                f"040000 tree {deep_tree}\td\n",
            ])
            destination = pathlib.Path(directory) / "tree"

            with self.assertRaises(IntegrationRejected) as raised:
                _materialize_tree(_Git(self.lab.root), commit, destination)

            self.assertEqual(raised.exception.reason_code, "candidate_tree_unsafe")
            self.assertEqual(list(outside.iterdir()), [])

    def test_duplicate_or_clashing_entries_are_a_rejection_not_a_raw_os_error(self) -> None:
        """F4: duplicate or clashing entries raised FileExistsError out of the integrator."""
        blob = self._blob("x")
        for lines in (
            [f"100644 blob {blob}\tf\n", f"100644 blob {blob}\tf\n"],
            [f"120000 blob {self._blob('target')}\tf\n", f"100644 blob {blob}\tf\n"],
        ):
            commit = self._crafted_commit(lines)
            with self.subTest(lines=lines), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(IntegrationRejected) as raised:
                    _materialize_tree(_Git(self.lab.root), commit, pathlib.Path(directory) / "tree")
                self.assertEqual(raised.exception.reason_code, "candidate_tree_unsafe")


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
