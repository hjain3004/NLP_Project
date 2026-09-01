"""Canonical hashing — exactly the two normative rules in ``agent-system/README.md``.

    canonical_json(obj) = json.dumps(obj, sort_keys=True,
                                     separators=(",", ":"), ensure_ascii=False)
    sha256_of_json(obj) = sha256(canonical_json(obj).encode("utf-8")).hexdigest()

A self-referential hash field is computed over the object with that field
removed: ``contract_hash`` covers the task contract minus ``contract_hash``;
``context_hash`` covers the context manifest minus ``context_hash``.

``sha256_of_tree`` walks paths in sorted order and hashes ``path\\0sha256\\n``
per file. Hashes must be stable across machines; a hash that changes with key
order is not a hash.

These primitives are load-bearing in Tasks 10, 11, 12, 13, 16, and 17. Nothing
here may be "improved" without a change to the README rule it implements.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

__all__ = [
    "canonical_json",
    "sha256_hex",
    "sha256_of_json",
    "sha256_of_json_excluding",
    "sha256_of_file",
    "sha256_of_tree",
]

_READ_CHUNK = 1 << 20  # 1 MiB


def canonical_json(obj: Any) -> str:
    """Return the one canonical textual form of ``obj``.

    Keys sorted, no insignificant whitespace, non-ASCII left as UTF-8 text
    (``ensure_ascii=False``) so ``"café"`` hashes identically regardless of the
    author's editor.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: bytes) -> str:
    """SHA-256 of raw bytes as lowercase hex."""
    return hashlib.sha256(data).hexdigest()


def sha256_of_json(obj: Any) -> str:
    """SHA-256 of the canonical JSON encoding of ``obj``."""
    return sha256_hex(canonical_json(obj).encode("utf-8"))


def sha256_of_json_excluding(obj: Mapping[str, Any], key: str) -> str:
    """SHA-256 of ``obj`` with ``key`` removed from the top level.

    The rule for every self-referential hash field. ``key`` need not be present;
    its absence yields the same result as hashing ``obj`` unchanged.
    """
    if not isinstance(obj, Mapping):
        raise TypeError(f"sha256_of_json_excluding expects a mapping, got {type(obj).__name__}")
    return sha256_of_json({k: v for k, v in obj.items() if k != key})


def sha256_of_file(path: str | os.PathLike[str]) -> str:
    """Streaming SHA-256 of a file's bytes."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_READ_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_of_tree(root: str | os.PathLike[str]) -> str:
    """Order-independent SHA-256 over every regular file under ``root``.

    For each file, relative to ``root`` with POSIX separators, in ascending
    path order, feed ``f"{path}\\0{sha256_of_file(path)}\\n"`` (UTF-8) into a
    running SHA-256. Directories and symlinks contribute nothing directly; only
    the files they contain do.
    """
    base = Path(root)
    if not base.is_dir():
        raise NotADirectoryError(f"not a directory: {base}")

    entries: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort()
        for name in filenames:
            absolute = Path(dirpath) / name
            if absolute.is_symlink() or not absolute.is_file():
                continue
            relative = absolute.relative_to(base).as_posix()
            entries.append((relative, absolute))

    digest = hashlib.sha256()
    for relative, absolute in sorted(entries, key=lambda item: item[0]):
        line = f"{relative}\0{sha256_of_file(absolute)}\n"
        digest.update(line.encode("utf-8"))
    return digest.hexdigest()
