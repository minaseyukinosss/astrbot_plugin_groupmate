"""Framework-independent domain values used by the Groupmate workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class TriggerKind(StringEnum):
    IGNORE = "ignore"
    COMMAND = "command"
    NATIVE_DIRECT = "native_direct"
    ALIAS_DIRECT = "alias_direct"
    COPIED_AT = "copied_at"
    CONTINUATION = "continuation"
    ALIAS_MENTION = "alias_mention"
    CANDIDATE = "candidate"


class DecisionAction(StringEnum):
    RESPOND = "respond"
    IGNORE = "ignore"


class Urgency(StringEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class MemoryKind(StringEnum):
    PROFILE = "profile"
    EPISODIC = "episodic"


@dataclass(frozen=True)
class ChatMessage:
    message_id: str
    group_id: str
    sender_id: str
    sender_name: str
    text: str
    timestamp: int
    reply_to_message_id: Optional[str] = None
    reply_to_bot: bool = False
    mentions_bot: bool = False
    is_bot: bool = False
    is_command: bool = False
    image_urls: Tuple[str, ...] = ()
    segment_types: Tuple[str, ...] = ()
    metadata: Dict[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_id", str(self.message_id))
        object.__setattr__(self, "group_id", str(self.group_id))
        object.__setattr__(self, "sender_id", str(self.sender_id))
        object.__setattr__(self, "sender_name", (self.sender_name or "").strip())
        object.__setattr__(self, "text", (self.text or "").strip())
        object.__setattr__(self, "image_urls", tuple(self.image_urls))
        object.__setattr__(self, "segment_types", tuple(self.segment_types))

    @property
    def identity(self) -> Tuple[str, str]:
        return self.group_id, self.message_id

    @property
    def has_content(self) -> bool:
        return bool(self.text or self.image_urls or self.segment_types)


@dataclass(frozen=True)
class TopicSnapshot:
    topic_id: str
    group_id: str
    messages: Tuple[ChatMessage, ...]
    created_at: int
    updated_at: int

    @property
    def latest(self) -> Optional[ChatMessage]:
        return self.messages[-1] if self.messages else None


@dataclass(frozen=True)
class Decision:
    action: DecisionAction
    confidence: float
    trigger: TriggerKind = TriggerKind.CANDIDATE
    reason_code: str = ""
    target_message_id: Optional[str] = None
    contribution: str = ""
    needs_vision: bool = False
    urgency: Urgency = Urgency.NORMAL

    @classmethod
    def ignore(
        cls,
        reason_code: str,
        trigger: TriggerKind = TriggerKind.CANDIDATE,
    ) -> "Decision":
        return cls(
            action=DecisionAction.IGNORE,
            confidence=0.0,
            trigger=trigger,
            reason_code=reason_code,
        )

    @classmethod
    def respond(
        cls,
        contribution: str,
        confidence: float = 1.0,
        trigger: TriggerKind = TriggerKind.CANDIDATE,
        reason_code: str = "useful_contribution",
        target_message_id: Optional[str] = None,
        needs_vision: bool = False,
        urgency: Urgency = Urgency.NORMAL,
    ) -> "Decision":
        return cls(
            action=DecisionAction.RESPOND,
            confidence=max(0.0, min(1.0, float(confidence))),
            trigger=trigger,
            reason_code=reason_code,
            target_message_id=target_message_id,
            contribution=contribution.strip(),
            needs_vision=needs_vision,
            urgency=urgency,
        )


@dataclass(frozen=True)
class MemoryItem:
    memory_id: str
    group_id: str
    subject_id: str
    kind: MemoryKind
    text: str
    created_at: int
    expires_at: Optional[int] = None
    confidence: float = 1.0
    importance: float = 0.5
    authority: int = 0
    source_message_id: Optional[str] = None


@dataclass(frozen=True)
class ReplyPlan:
    decision_id: str
    group_id: str
    trigger: TriggerKind
    contribution: str
    target_message_id: Optional[str]
    urgency: Urgency
    persona_prompt: str
    image_urls: Tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowOutcome:
    decision_id: str
    sent: bool
    reason: str
    text: str = ""


@dataclass(frozen=True)
class GroupPolicy:
    aliases: Tuple[str, ...] = ("爱弥斯", "小爱", "飞行雪绒")
    handle_native_wake: bool = True
    history_limit: int = 100
    decision_threshold: float = 0.72
    spontaneous_hourly_limit: int = 6
    spontaneous_cooldown_seconds: int = 600
    debounce_min_seconds: float = 4.0
    debounce_max_seconds: float = 8.0
    topic_max_seconds: int = 12
    candidate_ttl_seconds: int = 20
    max_reply_chars: int = 60
    vision_enabled: bool = True
    continuation_seconds: int = 90
    humanize_delay_enabled: bool = True
    max_reply_segments: int = 2

