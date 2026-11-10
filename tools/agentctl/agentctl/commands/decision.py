"""Accept a fixed ADR only after its sealed approval is verified and consumed."""

from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import json
import os
import re
import tempfile
from pathlib import Path

from ..approval import ApprovalManager, resolve_approval_target
from ..config import load as load_config
from ..hashing import sha256_hex, sha256_of_file
from ..store import EventStore

NAME = "decision"
_ADR_ID = "ADR-0005"
_APPROVAL_ACTION = "consequential_adr_acceptance"
_APPROVAL_RELATIVE = "agent-system/work/P0/approvals/ADR-0005-acceptance.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentctl decision", add_help=False)
    sub = parser.add_subparsers(dest="operation")
    accept = sub.add_parser("accept", add_help=False)
    accept.add_argument("--adr", required=True)
    accept.add_argument("--approval", required=True)
    accept.add_argument("--scope", required=True)
    return parser


def _accepted_documents(adr: str, approval_relative: str, approval_id: str) -> tuple[str, str]:
    updated = re.sub(r"(?m)^status:\s*proposed\s*$", "status: accepted", adr, count=1)
    if updated == adr:
        raise ValueError("ADR-0005 is not still proposed")
    updated = updated.replace(
        "Human approval, as an invariant-adjacent security decision. Recorded at G0.",
        "Human approval, as an invariant-adjacent security decision. Recorded at G0.\n"
        f"Approval artifact: `{approval_relative}` (approval_id `{approval_id}`).",
        1,
    )
    if f"Approval artifact: `{approval_relative}` (approval_id `{approval_id}`)." not in updated:
        raise ValueError("accepted ADR lacks the exact typed approval-artifact reference")
    return updated, ""


@contextlib.contextmanager
def _acceptance_lock(root: Path):
    runtime = root / "agent-system" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    with (runtime / "adr-0005-acceptance.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_replace(path: Path, content: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _journal_path(root: Path) -> Path:
    return root / "agent-system" / "runtime" / "adr-0005-acceptance.json"


def _write_journal(path: Path, journal: dict[str, str]) -> None:
    _atomic_replace(path, (json.dumps(journal, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))


def _recover_journal(root: Path, manager: ApprovalManager) -> None:
    journal_path = _journal_path(root)
    if not journal_path.is_file():
        return
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if journal.get("adr") != _ADR_ID or journal.get("target_hash") != sha256_hex(
        base64.b64decode(journal["adr_before_b64"])
    ):
        raise ValueError("ADR acceptance journal has an unexpected target")
    if journal.get("adr_path") != "docs/decisions/ADR-0005-symmetric-approval-seal.md" or journal.get(
        "readme_path"
    ) != "docs/decisions/README.md":
        raise ValueError("ADR acceptance journal paths are not fixed targets")
    rows = manager.store.rows(
        "SELECT path, payload_json FROM artifacts WHERE artifact_id = ? AND artifact_type = 'human-approval'",
        (journal["approval_id"],),
    )
    if not rows:
        raise ValueError("ADR acceptance journal has no registered approval")
    approval = json.loads(rows[0]["payload_json"])
    if rows[0]["path"] != journal.get("approval_relative") or journal.get(
        "approval_relative"
    ) != _APPROVAL_RELATIVE:
        raise ValueError("ADR acceptance journal approval path is not registered")
    if (
        approval.get("authorized_action") != _APPROVAL_ACTION
        or approval.get("target_type") != "adr"
        or approval.get("target_id") != _ADR_ID
        or approval.get("target_hash") != journal["target_hash"]
        or approval.get("scope") != journal["scope"]
    ):
        raise ValueError("ADR acceptance journal approval binding is invalid")
    consumed = bool(approval.get("consumed_at"))
    adr_path = root / journal["adr_path"]
    readme_path = root / journal["readme_path"]
    adr_before = base64.b64decode(journal["adr_before_b64"])
    readme_before = base64.b64decode(journal["readme_before_b64"])
    approval_relative = journal["approval_relative"]
    expected_adr_text, _ = _accepted_documents(
        adr_before.decode("utf-8"), approval_relative, journal["approval_id"]
    )
    expected_readme_text = readme_before.decode("utf-8").replace(
        "| ADR-0005 | Symmetric approval seal at Level 1 | proposed |",
        "| ADR-0005 | Symmetric approval seal at Level 1 | accepted |",
        1,
    )
    adr_after = expected_adr_text.encode("utf-8")
    readme_after = expected_readme_text.encode("utf-8")
    if (
        base64.b64decode(journal["adr_after_b64"]) != adr_after
        or base64.b64decode(journal["readme_after_b64"]) != readme_after
    ):
        raise ValueError("ADR acceptance journal after-state is not the fixed transition")
    adr_current = adr_path.read_bytes()
    readme_current = readme_path.read_bytes()
    allowed_adr = {sha256_hex(adr_before), sha256_hex(adr_after)}
    allowed_readme = {sha256_hex(readme_before), sha256_hex(readme_after)}
    if sha256_hex(adr_current) not in allowed_adr or sha256_hex(readme_current) not in allowed_readme:
        raise ValueError("ADR acceptance recovery found an unexpected external document edit")
    desired = (adr_after, readme_after) if consumed else (
        adr_before, readme_before
    )
    _atomic_replace(adr_path, desired[0])
    _atomic_replace(readme_path, desired[1])
    journal_path.unlink()


def run(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    if args.operation != "accept" or args.adr != _ADR_ID:
        raise SystemExit("usage: agentctl decision accept --adr ADR-0005 --approval <path> --scope <exact scope>")
    root = load_config().repo_root.resolve()
    approval_path = (root / args.approval).resolve()
    if approval_path.relative_to(root).as_posix() != _APPROVAL_RELATIVE:
        raise ValueError(f"ADR-0005 approval must be stored at {_APPROVAL_RELATIVE}")
    manager = ApprovalManager(EventStore(repository_root=root))
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    with _acceptance_lock(root):
        _recover_journal(root, manager)
        target = resolve_approval_target("adr", _ADR_ID, repository_root=root)
        if approval.get("target_hash") != target.target_hash:
            raise ValueError("approval target hash does not match current proposed ADR-0005 bytes")
        manager.register(approval, path=approval_path.relative_to(root).as_posix())
        adr_path = target.path
        adr_before_bytes = adr_path.read_bytes()
        adr_before = adr_before_bytes.decode("utf-8")
        readme_path = root / "docs/decisions/README.md"
        readme_before_bytes = readme_path.read_bytes()
        readme_before = readme_before_bytes.decode("utf-8")
        if sha256_of_file(adr_path) != target.target_hash:
            raise ValueError("ADR-0005 changed while preparing acceptance")
        adr_after, _ = _accepted_documents(
            adr_before, approval_path.relative_to(root).as_posix(), str(approval["approval_id"])
        )
        readme_after = readme_before.replace(
            "| ADR-0005 | Symmetric approval seal at Level 1 | proposed |",
            "| ADR-0005 | Symmetric approval seal at Level 1 | accepted |",
            1,
        )
        if readme_after == readme_before:
            raise ValueError("ADR-0005 index row is missing or already changed")
        journal_path = _journal_path(root)
        _write_journal(journal_path, {
            "adr": _ADR_ID,
            "approval_id": str(approval["approval_id"]),
            "target_hash": target.target_hash,
            "scope": args.scope,
            "approval_relative": approval_path.relative_to(root).as_posix(),
            "adr_path": adr_path.relative_to(root).as_posix(),
            "readme_path": readme_path.relative_to(root).as_posix(),
            "adr_before_b64": base64.b64encode(adr_before_bytes).decode("ascii"),
            "adr_after_b64": base64.b64encode(adr_after.encode("utf-8")).decode("ascii"),
            "readme_before_b64": base64.b64encode(readme_before_bytes).decode("ascii"),
            "readme_after_b64": base64.b64encode(readme_after.encode("utf-8")).decode("ascii"),
        })
        try:
            manager.consume(
                str(approval["approval_id"]),
                expected_action=_APPROVAL_ACTION,
                expected_target_type="adr",
                expected_target_id=_ADR_ID,
                expected_target_hash=target.target_hash,
                expected_scope=args.scope,
            )
            if sha256_of_file(adr_path) != sha256_hex(adr_before_bytes) or sha256_of_file(
                readme_path
            ) != sha256_hex(readme_before_bytes):
                raise ValueError("ADR or decision index changed during approval consumption")
            _atomic_replace(adr_path, adr_after.encode("utf-8"))
            _atomic_replace(readme_path, readme_after.encode("utf-8"))
            journal_path.unlink()
        except Exception:
            raise
    print(json.dumps({
        "status": "accepted",
        "adr": _ADR_ID,
        "approval_id": approval["approval_id"],
        "approved_target_hash": target.target_hash,
        "accepted_file_hash": sha256_of_file(adr_path),
        "approval_consumed": True,
    }, sort_keys=True, separators=(",", ":")))
    return 0
