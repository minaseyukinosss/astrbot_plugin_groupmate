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
    ReplyMode,
    ReplyPlan,
    SendResult,
    SocialEvent,
    TargetingDecision,
    TopicSnapshot,
)
from .core.context_assembly import AssembledPrompt
from .core.response_act import ResponseAct, ResponseActPlan
from .core.session import GroupSession


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
        reply_mode: Optional[ReplyMode] = None,
        response_act: Optional[ResponseAct] = None,
        capability_status=None,
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

class HistoryPort(Protocol):
    async def fetch_recent(self, group_id: str, count: int) -> Sequence[ChatMessage]:
        ...


class MemoryRepository(Protocol):
    def save_message(self, persona_id: str, message: ChatMessage) -> bool:
        ...

    def recent_messages(
        self, persona_id: str, group_id: str, limit: int
    ) -> Sequence[ChatMessage]:
        ...

    def add_memory(self, persona_id: str, memory: MemoryItem) -> None:
        ...

    def search_memories(
        self,
        persona_id: str,
        group_id: str,
        query: str,
        now: int,
        limit: int,
        subject_id: Optional[str] = None,
        subject_ids: Optional[Sequence[str]] = None,
        include_user_in_group: bool = True,
    ) -> Sequence[MemoryItem]:
        ...

    def record_transition(
        self,
        persona_id: str,
        decision_id: str,
        group_id: str,
        state: str,
        reason: str,
        timestamp: int,
    ) -> None:
        ...

    async def enqueue_outbox_async(
        self,
        persona_id: str,
        decision_id: str,
        group_id: str,
        text: str,
        created_at: int,
        expires_at: Optional[int] = None,
        *,
        quote_message_id: Optional[str] = None,
        segments: Sequence[str] = (),
        outbound: Sequence[OutboundSegment] = (),
        kind: str = "reply",
    ) -> bool:
        ...

    async def transition_outbox_async(
        self,
        persona_id: str,
        decision_id: str,
        expected: str,
        status: str,
        *,
        failure_code: str = "",
        failure_detail: str = "",
        increment_attempt: bool = False,
    ) -> bool:
        ...

    async def finalize_delivery_async(
        self,
        persona_id: str,
        decision_id: str,
        sent_at: int,
        bot_message: ChatMessage,
        reason: str = "sent",
    ) -> bool:
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

    def assemble(
        self,
        topic: TopicSnapshot,
        memories: Sequence[MemoryItem],
        *,
        contribution: str = "",
        soft_trigger: bool = False,
        session: Optional[GroupSession] = None,
        relationship_state: Optional[RelationshipState] = None,
        targeting: Optional[TargetingDecision] = None,
        reply_mode: Optional[ReplyMode] = None,
        response_act: Optional[ResponseActPlan] = None,
        capability_facts: Sequence[str] = (),
        capability_status: str = "",
    ) -> AssembledPrompt:
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
