"""Path-language overlap and semantic-lock pinning tests."""

from __future__ import annotations

import pathlib
import subprocess
import unittest

from agentctl.errors import ContractStale, PlanningError
from agentctl.locks import (
    SemanticLockConflict,
    SemanticLockSet,
    path_patterns_overlap,
    write_sets_conflict,
)


def _repo_root() -> pathlib.Path:
    output = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()
    return pathlib.Path(output)


class PathLockTests(unittest.TestCase):
    def test_identical_literal_paths_conflict(self) -> None:
        self.assertTrue(path_patterns_overlap("a/x.json", "a/x.json"))

    def test_glob_containment_conflicts(self) -> None:
        self.assertTrue(path_patterns_overlap("a/**", "a/b/c"))
        self.assertTrue(path_patterns_overlap("a/*.json", "a/x.json"))

    def test_disjoint_paths_do_not_conflict(self) -> None:
        self.assertFalse(path_patterns_overlap("a/*.json", "a/x.md"))

    def test_two_globs_conflict_only_when_they_have_a_common_match(self) -> None:
        self.assertTrue(path_patterns_overlap("a/foo.*", "a/*.json"))
        self.assertFalse(path_patterns_overlap("a/*.json", "a/*.md"))
        self.assertFalse(path_patterns_overlap("a/[ab].json", "a/[cd].json"))

    def test_double_star_matches_zero_or_many_segments(self) -> None:
        self.assertTrue(path_patterns_overlap("a/**", "a"))
        self.assertTrue(path_patterns_overlap("a/**/z", "a/b/c/z"))
        self.assertFalse(path_patterns_overlap("a/**/z", "b/z"))

    def test_write_sets_conflict_when_any_pair_overlaps(self) -> None:
        self.assertTrue(
            write_sets_conflict(
                ["docs/*.md", "agent-system/**"],
                ["tools/**", "agent-system/policies/x.json"],
            )
        )
        self.assertFalse(write_sets_conflict(["docs/*.md"], ["docs/*.json"]))

    def test_unsafe_or_unnormalized_pattern_is_rejected(self) -> None:
        for pattern in ("**", "/absolute", "a/../b", "a//b"):
            with self.subTest(pattern=pattern), self.assertRaises(ValueError):
                path_patterns_overlap(pattern, "safe/path")


class SemanticLockTests(unittest.TestCase):
    root = _repo_root()

    def setUp(self) -> None:
        self.locks = SemanticLockSet.from_policy(self.root)

    def test_exclusive_lock_rejects_a_second_owner(self) -> None:
        contract_hash = "a" * 64
        self.locks.acquire("TASK-1", {"contract:core-domain-v1": contract_hash})
        with self.assertRaises(SemanticLockConflict) as caught:
            self.locks.acquire("TASK-2", {"contract:core-domain-v1": contract_hash})
        self.assertEqual(caught.exception.code, "semantic_lock_conflict")
        self.assertEqual(caught.exception.owner_task_id, "TASK-1")

    def test_pinned_lock_requires_a_contract_hash(self) -> None:
        with self.assertRaises(PlanningError):
            self.locks.acquire("TASK-1", {"contract:core-domain-v1": None})

    def test_same_owner_cannot_silently_change_a_pinned_hash(self) -> None:
        self.locks.acquire("TASK-1", {"contract:core-domain-v1": "a" * 64})
        with self.assertRaises(ContractStale):
            self.locks.acquire("TASK-1", {"contract:core-domain-v1": "b" * 64})

    def test_same_owner_same_hash_is_idempotent_and_release_frees_lock(self) -> None:
        claims = {"contract:core-domain-v1": "a" * 64}
        first = self.locks.acquire("TASK-1", claims)
        second = self.locks.acquire("TASK-1", claims)
        self.assertEqual(first, second)
        self.locks.release("TASK-1")
        acquired = self.locks.acquire("TASK-2", claims)
        self.assertEqual(acquired[0].owner_task_id, "TASK-2")

    def test_unpinned_policy_lock_does_not_require_hash(self) -> None:
        claim = self.locks.acquire("TASK-1", {"repo:path-layout": None})
        self.assertIsNone(claim[0].contract_hash)

    def test_unknown_lock_is_a_planning_error(self) -> None:
        with self.assertRaises(PlanningError):
            self.locks.acquire("TASK-1", {"unknown:lock": "a" * 64})


if __name__ == "__main__":
    unittest.main()
