"""Bounded per-group working context and topic snapshots."""

from __future__ import annotations

from collections import deque
from typing import Deque, Iterable, Optional, Set, Tuple
from uuid import uuid4

from ..core.history_format import (
    ACTIVE_CONTEXT_MAX_MESSAGES,
    TOPIC_IDLE_GAP_SECONDS,
    select_active_messages,
)
from ..models import ChatMessage, TopicSnapshot

__all__ = [
    "ACTIVE_CONTEXT_MAX_MESSAGES",
    "TOPIC_IDLE_GAP_SECONDS",
    "TopicWindow",
    "select_active_messages",
]


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

    def reset_topic(self, topic_id: Optional[str] = None) -> str:
        self.topic_id = topic_id or uuid4().hex
        self._created_at = 0
        self._updated_at = 0
        return self.topic_id

    def hydrate(
        self,
        *,
        topic_id: str,
        created_at: int,
        updated_at: int,
        messages: Iterable[ChatMessage],
    ) -> None:
        self.topic_id = str(topic_id)
        self._messages = deque()
        self._seen = set()
        self._seen_order = deque()
        self._created_at = int(created_at or 0)
        self._updated_at = int(updated_at or 0)
        for message in messages:
            self.append(message)
        if self._messages and not self._created_at:
            self._created_at = self._messages[0].timestamp
        if self._messages:
            self._updated_at = max(
                self._updated_at, max(item.timestamp for item in self._messages)
            )

    def _remember_identity(self, identity: Tuple[str, str]) -> None:
        seen_limit = max(self.max_messages * 4, 100)
        while len(self._seen_order) >= seen_limit:
            expired = self._seen_order.popleft()
            self._seen.discard(expired)
        self._seen.add(identity)
        self._seen_order.append(identity)
