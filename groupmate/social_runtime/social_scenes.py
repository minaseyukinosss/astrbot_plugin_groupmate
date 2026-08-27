"""Frozen, evidence-bound descriptions of the current social scene."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


MAX_SCENE_REFERENCES = 32


class TargetScope(str, Enum):
    INDIVIDUAL = "INDIVIDUAL"
    GROUP = "GROUP"
    AMBIENT = "AMBIENT"


class ChorusTarget(str, Enum):
    NONE = "NONE"
    SELF = "SELF"
    MEMBER = "MEMBER"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class ChorusTone(str, Enum):
    NONE = "NONE"
    SAFE_BANTER = "SAFE_BANTER"
    SENSITIVE = "SENSITIVE"
    ATTACK = "ATTACK"
    DANGEROUS = "DANGEROUS"
    UNKNOWN = "UNKNOWN"


def _required_text(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _unique_texts(values: Iterable[object], name: str) -> tuple[str, ...]:
    result = tuple(
        dict.fromkeys(str(value).strip() for value in values if str(value).strip())
    )
    if len(result) > MAX_SCENE_REFERENCES:
        raise ValueError(f"{name} exceeds {MAX_SCENE_REFERENCES} items")
    return result


@dataclass(frozen=True)
class SocialScene:
    scene_kind: str
    target_scope: TargetScope
    target_id: str | None
    literal_subject: str
    user_move: str
    continuity_event_ids: tuple[str, ...]
    repetition_count: int = 0
    chorus_target: ChorusTarget = ChorusTarget.NONE
    chorus_target_id: str | None = None
    chorus_chain_id: str | None = None
    chorus_payload: str | None = None
    chorus_event_ids: tuple[str, ...] = ()
    chorus_participant_ids: tuple[str, ...] = ()
    chorus_already_joined: bool = False
    chorus_tone: ChorusTone = ChorusTone.NONE
    constraints: tuple[str, ...] = ()
    information_gaps: tuple[str, ...] = ()
    capability_request: str = "NONE"
    confidence: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "scene_kind", _required_text(self.scene_kind, "scene_kind"))
        object.__setattr__(self, "target_scope", TargetScope(self.target_scope))
        object.__setattr__(self, "target_id", _optional_text(self.target_id))
        object.__setattr__(
            self, "literal_subject", _required_text(self.literal_subject, "literal_subject")
        )
        object.__setattr__(self, "user_move", _required_text(self.user_move, "user_move"))
        object.__setattr__(
            self,
            "continuity_event_ids",
            _unique_texts(self.continuity_event_ids, "continuity_event_ids"),
        )
        object.__setattr__(self, "chorus_target", ChorusTarget(self.chorus_target))
        object.__setattr__(self, "chorus_tone", ChorusTone(self.chorus_tone))
        object.__setattr__(self, "chorus_target_id", _optional_text(self.chorus_target_id))
        object.__setattr__(self, "chorus_chain_id", _optional_text(self.chorus_chain_id))
        object.__setattr__(self, "chorus_payload", _optional_text(self.chorus_payload))
        object.__setattr__(
            self,
            "chorus_event_ids",
            _unique_texts(self.chorus_event_ids, "chorus_event_ids"),
        )
        object.__setattr__(
            self,
            "chorus_participant_ids",
            _unique_texts(self.chorus_participant_ids, "chorus_participant_ids"),
        )
        object.__setattr__(self, "constraints", _unique_texts(self.constraints, "constraints"))
        object.__setattr__(
            self,
            "information_gaps",
            _unique_texts(self.information_gaps, "information_gaps"),
        )
        object.__setattr__(
            self,
            "capability_request",
            _required_text(self.capability_request, "capability_request"),
        )

        repetition_count = int(self.repetition_count)
        if repetition_count < 0:
            raise ValueError("repetition_count must not be negative")
        object.__setattr__(self, "repetition_count", repetition_count)
        confidence = float(self.confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "confidence", confidence)

        if self.target_scope is TargetScope.INDIVIDUAL and self.target_id is None:
            raise ValueError("INDIVIDUAL scene requires target_id")
        if not self.continuity_event_ids:
            raise ValueError("continuity_event_ids must not be empty")
        self._validate_chorus()

    def _validate_chorus(self) -> None:
        has_evidence = self.chorus_chain_id is not None
        evidence_fields_present = bool(
            self.chorus_payload
            or self.chorus_event_ids
            or self.chorus_participant_ids
            or self.chorus_target is not ChorusTarget.NONE
            or self.chorus_tone is not ChorusTone.NONE
            or self.chorus_already_joined
        )
        if has_evidence != evidence_fields_present:
            raise ValueError("chorus fields must form one complete evidence set")
        if not has_evidence:
            if self.chorus_target_id is not None:
                raise ValueError("chorus_target_id requires chorus evidence")
            return

        # 靶心是语义判断；只有 MEMBER 才能绑定群成员，避免把模型猜测当成关系对象。
        if self.chorus_target is ChorusTarget.MEMBER:
            if self.chorus_target_id is None:
                raise ValueError("MEMBER chorus requires chorus_target_id")
        elif self.chorus_target_id is not None:
            raise ValueError("chorus_target_id is only valid for MEMBER chorus")
        if self.chorus_target is ChorusTarget.NONE or self.chorus_tone is ChorusTone.NONE:
            raise ValueError("chorus target and tone are required")
        if not self.chorus_payload:
            raise ValueError("chorus_payload must not be empty")
        if len(self.chorus_event_ids) < 2 or len(self.chorus_participant_ids) < 2:
            raise ValueError("chorus evidence requires two events and participants")
        if not set(self.chorus_event_ids).issubset(self.continuity_event_ids):
            raise ValueError("chorus_event_ids must be a subset of continuity_event_ids")
        if self.repetition_count < 2:
            raise ValueError("chorus repetition_count must be at least 2")

    @classmethod
    def create(cls, **values: object) -> "SocialScene":
        return cls(**values)


__all__ = (
    "ChorusTarget",
    "ChorusTone",
    "SocialScene",
    "TargetScope",
)
