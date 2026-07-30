"""Typed dependency boundaries for Companion Core workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

try:
    from typing import Protocol
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol

from .models import (
    ChatMessage,
    MemoryItem,
    OutboundSegment,
    RelationshipState,
    ReplyPlan,
    SendResult,
    SocialEvent,
    TopicSnapshot,
)


@dataclass(frozen=True)
class GuardResult:
    accepted: bool
    text: str
    codes: Tuple[str, ...]
    repairable: bool


class OutputGuard(Protocol):
    def validate(
        self,
        text: str,
        recent_outputs: Sequence[str],
        *,
        reply_mode=None,
    ) -> GuardResult:
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
    async def send_outbound(
        self,
        group_id: str,
        segments: Sequence[OutboundSegment],
        decision_id: str,
        quote_message_id: Optional[str] = None,
    ) -> SendResult:
        ...

    async def send_text(
        self,
        group_id: str,
        text: str,
        decision_id: str,
    ) -> SendResult:
        ...

    async def send_segments(
        self,
        group_id: str,
        segments: Sequence[str],
        decision_id: str,
        quote_message_id: Optional[str] = None,
    ) -> SendResult:
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

    def append_social_event(self, persona_id: str, event: SocialEvent) -> bool:
        ...

    def list_social_events(
        self,
        persona_id: str,
        group_id: str,
        user_id: Optional[str] = None,
        limit: int = 200,
    ):
        ...

    def get_relationship_state(
        self, persona_id: str, group_id: str, user_id: str
    ) -> Optional[RelationshipState]:
        ...

    def upsert_relationship_state(
        self, persona_id: str, state: RelationshipState
    ) -> None:
        ...

    def rebuild_relationship_state(
        self,
        persona_id: str,
        group_id: str,
        user_id: str,
        *,
        configured_relationship: Optional[str] = None,
        seed_affinity: int = 0,
        now: int = 0,
    ):
        ...

    def record_social_interaction(
        self,
        persona_id: str,
        event,
        *,
        configured_relationship: Optional[str] = None,
        now: int = 0,
    ):
        ...


class PersonaProvider(Protocol):
    async def system_prompt(self, group_id: str) -> str:
        ...

    def assemble(self, topic: TopicSnapshot, memories: Sequence[MemoryItem], **kwargs):
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
