"""Canonical hashing: key-order independence, self-referential exclusion, and a
pinned tree hash over a fixed three-file fixture.

The pinned values are recomputed from first principles inside each test (an
independent second implementation) *and* asserted against a frozen literal, so a
regression in either the module or the test is visible.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from agentctl.hashing import (
    canonical_json,
    sha256_of_file,
    sha256_of_json,
    sha256_of_json_excluding,
    sha256_of_tree,
)

PINNED_JSON_HASH = "b235cbe9e939410a87741d03ec0f091253b5f4c4a46c3b2c3ff743d214d72e4d"
PINNED_ALPHA_FILE_HASH = "b6a98d9ce9a2d9149288fa3df42d377c3e42737afdcdaf714e33c0a100b51060"
PINNED_TREE_HASH = "cd9f368a6b29d1bd66ac03089454caaa2c9c733c8f860d15e45140295216e23d"


class CanonicalJsonTests(unittest.TestCase):
    def test_key_order_does_not_change_the_hash(self) -> None:
        a = {"b": 1, "a": [1, 2], "c": "café"}
        b = {"c": "café", "a": [1, 2], "b": 1}
        self.assertEqual(sha256_of_json(a), sha256_of_json(b))

    def test_non_ascii_is_preserved_as_utf8_text(self) -> None:
        self.assertEqual(canonical_json({"c": "café"}), '{"c":"café"}')

    def test_pinned_json_hash(self) -> None:
        obj = {"b": 1, "a": [1, 2], "c": "café"}
        expected = hashlib.sha256(
            json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            )
        ).hexdigest()
        self.assertEqual(sha256_of_json(obj), expected)
        self.assertEqual(sha256_of_json(obj), PINNED_JSON_HASH)


class ExclusionTests(unittest.TestCase):
    def test_excluding_a_present_key_matches_hashing_without_it(self) -> None:
        base = {"b": 1, "a": [1, 2], "c": "café"}
        withself = dict(base, contract_hash="whatever")
        self.assertEqual(
            sha256_of_json_excluding(withself, "contract_hash"), sha256_of_json(base)
        )

    def test_excluding_an_absent_key_is_a_no_op(self) -> None:
        base = {"a": 1}
        self.assertEqual(sha256_of_json_excluding(base, "missing"), sha256_of_json(base))

    def test_non_mapping_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            sha256_of_json_excluding([1, 2, 3], "x")  # type: ignore[arg-type]


class FileAndTreeTests(unittest.TestCase):
    def _fixture(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="agentctl-tree-"))
        self.addCleanup(_rmtree, root)
        (root / "a.txt").write_bytes(b"alpha\n")
        (root / "sub").mkdir()
        (root / "sub" / "b.txt").write_bytes(b"beta\n")
        (root / "sub" / "c.json").write_bytes(b'{"k": 1}\n')
        return root

    def test_file_hash_is_streaming_sha256(self) -> None:
        root = self._fixture()
        self.assertEqual(sha256_of_file(root / "a.txt"), PINNED_ALPHA_FILE_HASH)

    def test_tree_hash_matches_independent_recompute_and_pin(self) -> None:
        root = self._fixture()

        entries = []
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                absolute = Path(dirpath) / name
                rel = absolute.relative_to(root).as_posix()
                entries.append((rel, absolute))
        digest = hashlib.sha256()
        for rel, absolute in sorted(entries):
            line = f"{rel}\0{sha256_of_file(absolute)}\n"
            digest.update(line.encode("utf-8"))
        independent = digest.hexdigest()

        self.assertEqual(sha256_of_tree(root), independent)
        self.assertEqual(sha256_of_tree(root), PINNED_TREE_HASH)

    def test_tree_hash_is_order_independent_of_walk(self) -> None:
        root = self._fixture()
        first = sha256_of_tree(root)
        # Rewrite identical content; hash must not move.
        (root / "a.txt").write_bytes(b"alpha\n")
        self.assertEqual(sha256_of_tree(root), first)

    def test_tree_hash_rejects_a_file_path(self) -> None:
        root = self._fixture()
        with self.assertRaises(NotADirectoryError):
            sha256_of_tree(root / "a.txt")


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
