"""``agentctl hash <path>`` — print the canonical hash of a file.

Plain files hash as raw bytes (``sha256_of_file``). With ``--json`` the file is
parsed and hashed as canonical JSON (``sha256_of_json``), so a reformatted but
semantically identical artifact produces the same digest. With ``--tree`` the
path must be a directory and is hashed with ``sha256_of_tree``.

Output is one line: ``<hex>  <path>`` — the two-space separator matches
``shasum`` so existing tooling can consume it. ``--digest-only`` emits the
bare 64-character digest for command substitution and approval target binding.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ..hashing import sha256_of_file, sha256_of_json, sha256_of_tree

NAME = "hash"


def run(argv: list[str]) -> int:
    mode = "bytes"
    digest_only = False
    positional: list[str] = []
    for arg in argv:
        if arg == "--json":
            mode = "json"
        elif arg == "--tree":
            mode = "tree"
        elif arg == "--digest-only":
            digest_only = True
        elif arg.startswith("-"):
            print(f"agentctl hash: unknown option {arg!r}", file=sys.stderr)
            return 2
        else:
            positional.append(arg)

    if len(positional) != 1:
        print(
            "usage: agentctl hash [--json|--tree] [--digest-only] <path>",
            file=sys.stderr,
        )
        return 2

    target = Path(positional[0])
    if not target.exists():
        print(f"agentctl hash: no such path: {target}", file=sys.stderr)
        return 1

    if mode == "tree":
        digest = sha256_of_tree(target)
    elif mode == "json":
        digest = sha256_of_json(json.loads(target.read_text(encoding="utf-8")))
    else:
        digest = sha256_of_file(target)

    print(digest if digest_only else f"{digest}  {target}")
    return 0
