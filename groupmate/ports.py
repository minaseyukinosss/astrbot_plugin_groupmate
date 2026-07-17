"""Typed dependency boundaries for the Groupmate domain."""

from __future__ import annotations

from typing import Optional, Sequence

try:
    from typing import Protocol
except ImportError:  # pragma: no cover - Python 3.7 test environment only
    from typing_extensions import Protocol

from .models import (
    ChatMessage,
    Decision,
    GroupPolicy,
    MemoryItem,
    ReplyPlan,
    TopicSnapshot,
)


class DecisionModelPort(Protocol):
    async def decide(
        self,
        topic: TopicSnapshot,
        policy: GroupPolicy,
        memories: Sequence[MemoryItem],
    ) -> Decision:
        ...


class GenerationModelPort(Protocol):
    async def generate(
        self,
        plan: ReplyPlan,
        topic: TopicSnapshot,
        memories: Sequence[MemoryItem],
    ) -> str:
        ...

    async def repair(self, text: str, violations: Sequence[str]) -> str:
        ...


class VisionPort(Protocol):
    async def describe(self, image_urls: Sequence[str]) -> str:
        ...


class PlatformPort(Protocol):
    async def send_text(
        self,
        group_id: str,
        text: str,
        decision_id: str,
    ) -> None:
        ...


class HistoryPort(Protocol):
    async def fetch_recent(self, group_id: str, count: int) -> Sequence[ChatMessage]:
        ...


class MemoryRepository(Protocol):
    def save_message(self, message: ChatMessage) -> bool:
        ...

    def recent_messages(self, group_id: str, limit: int) -> Sequence[ChatMessage]:
        ...

    def add_memory(self, memory: MemoryItem) -> None:
        ...

    def search_memories(
        self,
        group_id: str,
        query: str,
        now: int,
        limit: int,
        subject_id: Optional[str] = None,
    ) -> Sequence[MemoryItem]:
        ...


class PersonaProvider(Protocol):
    async def system_prompt(self, group_id: str) -> str:
        ...

    def build_user_context(
        self,
        topic: TopicSnapshot,
        memories: Sequence[MemoryItem],
    ) -> str:
        ...


class TraceSink(Protocol):
    def record(
        self,
        decision_id: str,
        group_id: str,
        state: str,
        reason: str,
        timestamp: int,
    ) -> None:
        ...


class Clock(Protocol):
    def now(self) -> int:
        ...

