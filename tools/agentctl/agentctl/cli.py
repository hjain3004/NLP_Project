"""Thin command dispatcher.

``cli.main`` reads the first argument as a command name and hands the rest to
that command's ``run(argv) -> int``. Commands live one-per-file under
``agentctl/commands/`` and are discovered automatically, so Tasks 12-17 each add
a file and never touch this dispatcher.

A command module must define:

* ``NAME`` — the subcommand string;
* ``run(argv: list[str]) -> int`` — returns the process exit code.

Unknown commands exit 2. A raised :class:`~agentctl.errors.AgentctlError` is
printed to stderr with its ``code`` and exits 1; anything else propagates.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from typing import Callable

from . import commands as _commands_pkg
from .errors import AgentctlError

CommandFn = Callable[[list[str]], int]


def discover_commands() -> dict[str, CommandFn]:
    """Map every ``NAME`` under ``agentctl.commands`` to its ``run`` callable."""
    found: dict[str, CommandFn] = {}
    for info in pkgutil.iter_modules(_commands_pkg.__path__):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{_commands_pkg.__name__}.{info.name}")
        name = getattr(module, "NAME", None)
        run = getattr(module, "run", None)
        if isinstance(name, str) and callable(run):
            found[name] = run
    return found


#: Populated on first ``main`` call; exposed for tests and ``doctor``.
COMMANDS: dict[str, CommandFn] = {}


def _usage(stream) -> None:
    names = ", ".join(sorted(COMMANDS)) or "(none registered)"
    print(f"usage: agentctl <command> [args]\ncommands: {names}", file=stream)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    COMMANDS.clear()
    COMMANDS.update(discover_commands())

    if not argv or argv[0] in ("-h", "--help"):
        _usage(sys.stdout)
        return 0

    name, rest = argv[0], argv[1:]
    run = COMMANDS.get(name)
    if run is None:
        print(f"agentctl: unknown command {name!r}", file=sys.stderr)
        _usage(sys.stderr)
        return 2

    try:
        return int(run(rest))
    except AgentctlError as exc:
        print(f"agentctl: {exc.code}: {exc}", file=sys.stderr)
        return 1
