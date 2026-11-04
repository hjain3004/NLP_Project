"""``agentctl integrate [<task-id>]`` -- integrate the next verified, reviewed candidate.

With no task id the deterministic queue chooses the candidate. With a task id the
request proceeds only if that task is the queue head.

Exit codes: 0 integrated / already integrated / reconciled; 1 rejected or refused;
2 usage; 3 nothing eligible. The outcome is printed as one JSON object on stdout.
"""

from __future__ import annotations

import json
import os
import sys

from ..gate_runner import drop_sensitive_environment
from ..integrate import DEFAULT_BRANCH, IntegrationSettings, exit_code_for, integrate

NAME = "integrate"

_USAGE = (
    "usage: agentctl integrate [<task-id>] [--branch NAME] [--base REV] "
    "[--integrator-id ID] [--check-timeout SECONDS]"
)
_OPTIONS = {"--branch", "--base", "--integrator-id", "--check-timeout"}


def run(argv: list[str]) -> int:
    # The integrator reads no credential; never keep one where a gate could be handed it.
    drop_sensitive_environment()
    task_id: str | None = None
    options: dict[str, str] = {}
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument in _OPTIONS and index + 1 < len(argv):
            options[argument] = argv[index + 1]
            index += 2
        elif not argument.startswith("-") and task_id is None:
            task_id = argument
            index += 1
        else:
            print(_USAGE, file=sys.stderr)
            return 2
    try:
        timeout = float(options.get("--check-timeout", "600"))
    except ValueError:
        print(_USAGE, file=sys.stderr)
        return 2
    settings = IntegrationSettings(
        branch=options.get("--branch")
        or os.environ.get("AGENTCTL_INTEGRATION_BRANCH")
        or DEFAULT_BRANCH,
        base=options.get("--base"),
        integrator_id=options.get("--integrator-id"),
        check_timeout=timeout,
    )
    result = integrate(task_id, settings=settings)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    code = exit_code_for(result)
    if code != 0:
        print(
            f"agentctl: integrate: {result.get('outcome')}: {result.get('reason_code')}",
            file=sys.stderr,
        )
    return code
