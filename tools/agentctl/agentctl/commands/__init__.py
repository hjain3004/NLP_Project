"""Command modules, one subcommand per file.

Each module defines ``NAME`` and ``run(argv: list[str]) -> int``. ``agentctl.cli``
discovers them; nothing here needs a central registry. Tasks 12-17 add
``validate``, ``status``, ``dispatch``, ``ingest``, ``ready``, ``debug``,
``context``, ``review``, ``approve``, ``gate``, and ``doctor``.
"""
