"""Immutable contracts for offline export shadow evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from groupmate.core.response_act import ResponseAct
from groupmate.models import InteractionScene


def _sequence_tuple(value, name):
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError("{} must be a sequence, not a scalar string".format(name))
    if value is None:
        return ()
    try:
        return tuple(value)
    except TypeError:
        raise TypeError("{} must be a sequence".format(name))


def _string_tuple(value, name):
    values = _sequence_tuple(value, name)
    if not all(isinstance(item, str) for item in values):
        raise TypeError("{} must contain strings".format(name))
    return tuple(item.strip() for item in values)


def _require_instance(value, expected_type, name):
    if not isinstance(value, expected_type):
        raise TypeError(
            "{} must be {}".format(name, expected_type.__name__)
        )


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
        object.__setattr__(
            self,
            "element_types",
            _string_tuple(self.element_types, "element_types"),
        )
        object.__setattr__(
            self,
            "mentions",
            _string_tuple(self.mentions, "mentions"),
        )

    @property
    def content_eligible(self) -> bool:
        return not self.system and not self.recalled and bool(
            self.text or self.has_media or self.element_types
        )


def _event_tuple(value, name):
    values = _sequence_tuple(value, name)
    if not all(isinstance(item, ExportEvent) for item in values):
        raise TypeError("{} must contain ExportEvent values".format(name))
    return values


@dataclass(frozen=True)
class ExportSummary:
    manifest_records: int
    observed_records: int
    target_records: int
    excluded_system: int
    excluded_recalled: int
    duplicate_records: int
    chunk_count: int
    excluded_content_ineligible: int = 0


@dataclass(frozen=True)
class IngestResult:
    events: Tuple[ExportEvent, ...]
    summary: ExportSummary
    target_uin: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "events", _event_tuple(self.events, "events"))
        _require_instance(self.summary, ExportSummary, "summary")


@dataclass(frozen=True)
class ResponseRun:
    run_id: str
    events: Tuple[ExportEvent, ...]
    anchor_message_id: str
    confidence: AssociationConfidence
    reason_codes: Tuple[str, ...]
    review_reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "events", _event_tuple(self.events, "events"))
        _require_instance(
            self.confidence,
            AssociationConfidence,
            "confidence",
        )
        object.__setattr__(
            self,
            "reason_codes",
            _string_tuple(self.reason_codes, "reason_codes"),
        )

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

    def __post_init__(self) -> None:
        _require_instance(self.source, ExportEvent, "source")
        object.__setattr__(
            self,
            "context",
            _event_tuple(self.context, "context"),
        )
        if self.response_run is not None:
            _require_instance(self.response_run, ResponseRun, "response_run")


@dataclass(frozen=True)
class LocalReviewItem:
    sample_id: str
    reason: str
    source_events: Tuple[ExportEvent, ...]
    response_events: Tuple[ExportEvent, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_events",
            _event_tuple(self.source_events, "source_events"),
        )
        object.__setattr__(
            self,
            "response_events",
            _event_tuple(self.response_events, "response_events"),
        )


@dataclass(frozen=True)
class ReferenceLabel:
    scene: InteractionScene
    act: Optional[ResponseAct]
    confidence: AssociationConfidence
    reason_codes: Tuple[str, ...]

    def __post_init__(self) -> None:
        _require_instance(self.scene, InteractionScene, "scene")
        if self.act is not None:
            _require_instance(self.act, ResponseAct, "act")
        _require_instance(
            self.confidence,
            AssociationConfidence,
            "confidence",
        )
        object.__setattr__(
            self,
            "reason_codes",
            _string_tuple(self.reason_codes, "reason_codes"),
        )


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

    def __post_init__(self) -> None:
        _require_instance(self.scene, InteractionScene, "scene")
        if self.act is not None:
            _require_instance(self.act, ResponseAct, "act")
        object.__setattr__(
            self,
            "reason_codes",
            _string_tuple(self.reason_codes, "reason_codes"),
        )
