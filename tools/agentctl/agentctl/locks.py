"""Path-language and semantic-lock conflict detection."""

from __future__ import annotations

import json
import posixpath
import re
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import repo_root as resolve_repo_root
from .errors import ContractStale, PlanningError

__all__ = [
    "SemanticLockClaim",
    "SemanticLockConflict",
    "SemanticLockSet",
    "path_patterns_overlap",
    "write_sets_conflict",
]


TokenKind = Literal["star", "any", "positive", "negative"]


@dataclass(frozen=True)
class _CharacterToken:
    kind: TokenKind
    characters: frozenset[str] = frozenset()


def _normalise_pattern(pattern: str) -> tuple[str, ...]:
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("path pattern must be a nonempty string")
    if pattern == "**" or pattern.startswith("/") or "\\" in pattern:
        raise ValueError(f"unsafe path pattern: {pattern!r}")
    segments = pattern.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise ValueError(f"unnormalised path pattern: {pattern!r}")
    if posixpath.normpath(pattern) != pattern:
        raise ValueError(f"unnormalised path pattern: {pattern!r}")
    return tuple(segments)


def _class_characters(source: str) -> frozenset[str]:
    characters: set[str] = set()
    index = 0
    while index < len(source):
        if index + 2 < len(source) and source[index + 1] == "-":
            start, end = ord(source[index]), ord(source[index + 2])
            if start <= end:
                characters.update(chr(value) for value in range(start, end + 1))
                index += 3
                continue
        characters.add(source[index])
        index += 1
    return frozenset(characters)


def _tokenise_segment(pattern: str) -> tuple[_CharacterToken, ...]:
    tokens: list[_CharacterToken] = []
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "*":
            if not tokens or tokens[-1].kind != "star":
                tokens.append(_CharacterToken("star"))
            index += 1
        elif character == "?":
            tokens.append(_CharacterToken("any"))
            index += 1
        elif character == "[":
            closing = pattern.find("]", index + 1)
            if closing < 0 or closing == index + 1:
                tokens.append(_CharacterToken("positive", frozenset({"["})))
                index += 1
                continue
            body = pattern[index + 1 : closing]
            negative = body.startswith("!")
            if negative:
                body = body[1:]
            characters = _class_characters(body)
            tokens.append(_CharacterToken("negative" if negative else "positive", characters))
            index = closing + 1
        else:
            tokens.append(_CharacterToken("positive", frozenset({character})))
            index += 1
    return tuple(tokens)


def _character_tokens_intersect(left: _CharacterToken, right: _CharacterToken) -> bool:
    if left.kind == "any" or right.kind == "any":
        other = right if left.kind == "any" else left
        return other.kind in ("any", "negative") or bool(other.characters)
    if left.kind == "positive" and right.kind == "positive":
        return bool(left.characters & right.characters)
    if left.kind == "negative" and right.kind == "negative":
        return True
    positive = left if left.kind == "positive" else right
    negative = right if left.kind == "positive" else left
    return bool(positive.characters - negative.characters)


def _segments_overlap(left: str, right: str) -> bool:
    """NFA-product emptiness for two nonempty single-segment glob languages."""
    left_tokens = _tokenise_segment(left)
    right_tokens = _tokenise_segment(right)
    pending = deque([(0, 0, False)])
    visited: set[tuple[int, int, bool]] = set()
    while pending:
        left_index, right_index, consumed = pending.popleft()
        state = (left_index, right_index, consumed)
        if state in visited:
            continue
        visited.add(state)
        if (
            left_index == len(left_tokens)
            and right_index == len(right_tokens)
            and consumed
        ):
            return True

        left_token = left_tokens[left_index] if left_index < len(left_tokens) else None
        right_token = right_tokens[right_index] if right_index < len(right_tokens) else None
        if left_token is not None and left_token.kind == "star":
            pending.append((left_index + 1, right_index, consumed))
        if right_token is not None and right_token.kind == "star":
            pending.append((left_index, right_index + 1, consumed))
        if left_token is None or right_token is None:
            continue
        if left_token.kind == "star" and right_token.kind == "star":
            pending.append((left_index, right_index, True))
        elif left_token.kind == "star":
            pending.append((left_index, right_index + 1, True))
        elif right_token.kind == "star":
            pending.append((left_index + 1, right_index, True))
        elif _character_tokens_intersect(left_token, right_token):
            pending.append((left_index + 1, right_index + 1, True))
    return False


def path_patterns_overlap(left: str, right: str) -> bool:
    """Whether two normalized POSIX path globs can match a common path."""
    left_segments = _normalise_pattern(left)
    right_segments = _normalise_pattern(right)
    pending = deque([(0, 0)])
    visited: set[tuple[int, int]] = set()
    while pending:
        left_index, right_index = pending.popleft()
        state = (left_index, right_index)
        if state in visited:
            continue
        visited.add(state)
        if left_index == len(left_segments) and right_index == len(right_segments):
            return True

        left_segment = (
            left_segments[left_index] if left_index < len(left_segments) else None
        )
        right_segment = (
            right_segments[right_index] if right_index < len(right_segments) else None
        )
        if left_segment == "**":
            pending.append((left_index + 1, right_index))
        if right_segment == "**":
            pending.append((left_index, right_index + 1))
        if left_segment is None or right_segment is None:
            continue
        if left_segment == "**" and right_segment == "**":
            continue
        if left_segment == "**":
            pending.append((left_index, right_index + 1))
        elif right_segment == "**":
            pending.append((left_index + 1, right_index))
        elif _segments_overlap(left_segment, right_segment):
            pending.append((left_index + 1, right_index + 1))
    return False


def write_sets_conflict(left: Iterable[str], right: Iterable[str]) -> bool:
    """True when any pair of write patterns has a possible common match."""
    left_patterns = tuple(left)
    right_patterns = tuple(right)
    return any(
        path_patterns_overlap(left_pattern, right_pattern)
        for left_pattern in left_patterns
        for right_pattern in right_patterns
    )


@dataclass(frozen=True)
class SemanticLockClaim:
    lock_id: str
    owner_task_id: str
    contract_hash: str | None


class SemanticLockConflict(PlanningError):
    """An exclusive semantic lock is held by another task."""

    code = "semantic_lock_conflict"

    def __init__(self, lock_id: str, owner_task_id: str, requester_task_id: str) -> None:
        self.lock_id = lock_id
        self.owner_task_id = owner_task_id
        self.requester_task_id = requester_task_id
        super().__init__(
            "semantic lock is already held",
            lock_id=lock_id,
            owner_task_id=owner_task_id,
            requester_task_id=requester_task_id,
        )


class SemanticLockSet:
    """Atomic in-memory semantic claims, parameterized by the policy file."""

    def __init__(self, definitions: Mapping[str, Mapping[str, object]]) -> None:
        self.definitions = {lock_id: dict(definition) for lock_id, definition in definitions.items()}
        self._claims: dict[str, SemanticLockClaim] = {}

    @classmethod
    def from_policy(cls, repository_root: str | Path | None = None) -> "SemanticLockSet":
        root = (
            Path(repository_root).expanduser().resolve()
            if repository_root
            else resolve_repo_root()
        )
        policy_path = root / "agent-system" / "policies" / "semantic-locks.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        return cls({entry["lock_id"]: entry for entry in policy["entries"]})

    @property
    def claims(self) -> tuple[SemanticLockClaim, ...]:
        return tuple(self._claims[lock_id] for lock_id in sorted(self._claims))

    def acquire(
        self, task_id: str, requested: Mapping[str, str | None]
    ) -> tuple[SemanticLockClaim, ...]:
        """Atomically acquire all requested locks or change nothing."""
        proposed: list[SemanticLockClaim] = []
        for lock_id in sorted(requested):
            definition = self.definitions.get(lock_id)
            if definition is None:
                raise PlanningError("unknown semantic lock", lock_id=lock_id)
            contract_hash = requested[lock_id]
            if definition.get("pinned_by_contract_hash") and not contract_hash:
                raise PlanningError(
                    "semantic lock requires a pinned contract hash", lock_id=lock_id
                )
            if contract_hash is not None and re.fullmatch(r"[0-9a-f]{64}", contract_hash) is None:
                raise PlanningError(
                    "semantic lock contract hash must be lowercase SHA-256",
                    lock_id=lock_id,
                    contract_hash=contract_hash,
                )

            existing = self._claims.get(lock_id)
            if existing is not None and existing.owner_task_id != task_id:
                raise SemanticLockConflict(lock_id, existing.owner_task_id, task_id)
            if existing is not None and existing.contract_hash != contract_hash:
                raise ContractStale(
                    "semantic lock contract hash changed for its current owner",
                    lock_id=lock_id,
                    expected=existing.contract_hash,
                    actual=contract_hash,
                )
            proposed.append(existing or SemanticLockClaim(lock_id, task_id, contract_hash))

        for claim in proposed:
            self._claims[claim.lock_id] = claim
        return tuple(proposed)

    def release(self, task_id: str) -> tuple[SemanticLockClaim, ...]:
        released = tuple(
            claim for claim in self.claims if claim.owner_task_id == task_id
        )
        for claim in released:
            del self._claims[claim.lock_id]
        return released
