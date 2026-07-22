"""Bounded per-group working context and topic snapshots."""

from __future__ import annotations

from collections import deque
from typing import Deque, Sequence, Set, Tuple
from uuid import uuid4

from .models import ChatMessage, TopicSnapshot

ACTIVE_CONTEXT_MAX_MESSAGES = 8
TOPIC_IDLE_GAP_SECONDS = 120


def select_active_messages(
    messages: Sequence[ChatMessage],
    *,
    topic_created_at: int = 0,
    max_messages: int = ACTIVE_CONTEXT_MAX_MESSAGES,
    idle_gap_seconds: int = TOPIC_IDLE_GAP_SECONDS,
) -> Tuple[ChatMessage, ...]:
    """Pick the current-topic slice for decision/generation prompts.

    1. Drop messages older than the current topic boundary (`topic_created_at`).
    2. Walk backward from the latest message and cut on a large idle gap.
    3. Cap at `max_messages`, returning chronological order.
    """
    if max_messages < 1:
        return ()
    candidates = tuple(messages)
    if topic_created_at > 0:
        candidates = tuple(
            message for message in candidates if message.timestamp >= topic_created_at
        )
    if not candidates:
        return ()

    selected = [candidates[-1]]
    for index in range(len(candidates) - 2, -1, -1):
        if len(selected) >= max_messages:
            break
        newer = selected[-1]
        older = candidates[index]
        if newer.timestamp - older.timestamp > idle_gap_seconds:
            break
        selected.append(older)
    selected.reverse()
    return tuple(selected)


class TopicWindow:
    def __init__(self, group_id: str, max_messages: int = 100) -> None:
        if max_messages < 1:
            raise ValueError("max_messages must be positive")
        self.group_id = str(group_id)
        self.max_messages = max_messages
        self.topic_id = uuid4().hex
        self._messages: Deque[ChatMessage] = deque()
        self._seen: Set[Tuple[str, str]] = set()
        self._seen_order: Deque[Tuple[str, str]] = deque()
        self._created_at = 0
        self._updated_at = 0

    def append(self, message: ChatMessage) -> bool:
        if message.group_id != self.group_id:
            raise ValueError("message belongs to a different group")
        if message.identity in self._seen:
            return False

        self._remember_identity(message.identity)
        self._messages.append(message)
        while len(self._messages) > self.max_messages:
            self._messages.popleft()

        if not self._created_at:
            self._created_at = message.timestamp
        self._updated_at = max(self._updated_at, message.timestamp)
        return True

    def snapshot(self) -> TopicSnapshot:
        return TopicSnapshot(
            topic_id=self.topic_id,
            group_id=self.group_id,
            messages=tuple(self._messages),
            created_at=self._created_at,
            updated_at=self._updated_at,
        )

    def reset_topic(self) -> None:
        self.topic_id = uuid4().hex
        self._created_at = 0
        self._updated_at = 0

    def _remember_identity(self, identity: Tuple[str, str]) -> None:
        seen_limit = max(self.max_messages * 4, 100)
        while len(self._seen_order) >= seen_limit:
            expired = self._seen_order.popleft()
            self._seen.discard(expired)
        self._seen.add(identity)
        self._seen_order.append(identity)

