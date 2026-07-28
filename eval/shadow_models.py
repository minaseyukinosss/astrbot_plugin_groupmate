"""Immutable contracts for offline export shadow evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from groupmate.core.response_act import ResponseAct
from groupmate.models import InteractionScene


class AssociationConfidence(str, Enum):
    HIGH = "high"
    REVIEW = "review"


@dataclass(frozen=True)
class ExportEvent:
    message_id: str
    seq: int
    timestamp_ms: int
    sender_key: str
    sender_uin: str
    sender_name: str
    message_type: str
    text: str
    element_types: Tuple[str, ...]
    reply_to_message_id: str = ""
    reply_to_sender_uin: str = ""
    mentions: Tuple[str, ...] = ()
    has_media: bool = False
    recalled: bool = False
    system: bool = False

    def __post_init__(self) -> None:
        for name in ("message_id", "sender_key", "message_type"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError("{} is required".format(name))
            object.__setattr__(self, name, value)
        object.__setattr__(self, "seq", int(self.seq))
        object.__setattr__(self, "timestamp_ms", int(self.timestamp_ms))
        if self.timestamp_ms < 0:
            raise ValueError("timestamp_ms must be non-negative")
        for name in (
            "sender_uin",
            "sender_name",
            "text",
            "reply_to_message_id",
            "reply_to_sender_uin",
        ):
            object.__setattr__(
                self,
                name,
                str(getattr(self, name) or "").strip(),
            )
        object.__setattr__(self, "element_types", tuple(self.element_types or ()))
        object.__setattr__(self, "mentions", tuple(self.mentions or ()))

    @property
    def content_eligible(self) -> bool:
        return not self.system and not self.recalled and bool(
            self.text or self.has_media or self.element_types
        )


@dataclass(frozen=True)
class ExportSummary:
    manifest_records: int
    observed_records: int
    target_records: int
    excluded_system: int
    excluded_recalled: int
    duplicate_records: int
    chunk_count: int


@dataclass(frozen=True)
class IngestResult:
    events: Tuple[ExportEvent, ...]
    summary: ExportSummary
    target_uin: str


@dataclass(frozen=True)
class ResponseRun:
    run_id: str
    events: Tuple[ExportEvent, ...]
    anchor_message_id: str
    confidence: AssociationConfidence
    reason_codes: Tuple[str, ...]
    review_reason: str = ""

    @property
    def message_count(self) -> int:
        return len(self.events)

    @property
    def reply_chars(self) -> int:
        return sum(len(item.text) for item in self.events)

    @property
    def has_media(self) -> bool:
        return any(item.has_media for item in self.events)

    @property
    def quoted(self) -> bool:
        return any(bool(item.reply_to_message_id) for item in self.events)


@dataclass(frozen=True)
class BehaviorExample:
    sample_id: str
    source: ExportEvent
    context: Tuple[ExportEvent, ...]
    response_run: Optional[ResponseRun]
    observed_replied: bool
    covered_context: bool
    review_reason: str


@dataclass(frozen=True)
class LocalReviewItem:
    sample_id: str
    reason: str
    source_events: Tuple[ExportEvent, ...]
    response_events: Tuple[ExportEvent, ...]


@dataclass(frozen=True)
class ReferenceLabel:
    scene: InteractionScene
    act: Optional[ResponseAct]
    confidence: AssociationConfidence
    reason_codes: Tuple[str, ...]


@dataclass(frozen=True)
class ShadowProjection:
    sample_id: str
    owner: str
    would_reply: bool
    trigger: str
    scene: InteractionScene
    act: Optional[ResponseAct]
    quote_allowed: bool
    decorative_media_allowed: bool
    capability_media_allowed: bool
    ambiguous_target: bool
    owner_count: int
    completion_claim_allowed: bool
    reason_codes: Tuple[str, ...]
