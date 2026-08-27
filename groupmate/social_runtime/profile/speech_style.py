"""Immutable member speech-style contracts and local evidence policy.

The style asset describes how a member arranges a reply.  It deliberately
contains no Persona identity, member biography, opinions, or reusable quotes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping


_STYLE_STATUSES = frozenset({"READY", "FAILED"})
_SENSITIVE_PATTERNS = (
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?i)(?:password|passwd|密码|验证码)\s*[:：]?\s*\S+"),
    re.compile(r"\b\d{15,18}[0-9Xx]\b"),
)
_ATTACK_PATTERNS = re.compile(r"(?:去死|滚开|废物|傻逼|操你|杀了你)")
_COMMAND_PREFIXES = ("/", "!", "！", ".", "。")


def _required(value: object, label: str, *, maximum: int = 240) -> str:
    normalized = " ".join(str(value or "").split())
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{label} must contain 1-{maximum} characters")
    return normalized


def _bounded_tuple(
    values: tuple[str, ...], label: str, *, maximum_items: int = 12
) -> tuple[str, ...]:
    normalized = tuple(
        dict.fromkeys(_required(item, label) for item in tuple(values))
    )
    if not normalized or len(normalized) > maximum_items:
        raise ValueError(f"{label} must contain 1-{maximum_items} values")
    return normalized


@dataclass(frozen=True)
class MemberStyleSetting:
    group_id: str
    member_id: str
    enabled: bool
    enabled_at: int
    updated_by: str
    updated_at: int
    version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "group_id", _required(self.group_id, "group_id", maximum=120))
        object.__setattr__(self, "member_id", _required(self.member_id, "member_id", maximum=120))
        object.__setattr__(self, "enabled", bool(self.enabled))
        for name in ("enabled_at", "updated_at", "version"):
            object.__setattr__(self, name, max(0, int(getattr(self, name))))
        if self.enabled:
            object.__setattr__(
                self, "updated_by", _required(self.updated_by, "updated_by", maximum=120)
            )
        else:
            object.__setattr__(self, "updated_by", str(self.updated_by or "").strip())

    @classmethod
    def disabled(cls, group_id: str, member_id: str) -> "MemberStyleSetting":
        return cls(group_id, member_id, False, 0, "", 0, 0)


@dataclass(frozen=True)
class MemberStyleEvidence:
    event_id: str
    group_id: str
    member_id: str
    text: str
    occurred_at: int
    scene_type: str
    short_reaction: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _required(self.event_id, "event_id", maximum=180))
        object.__setattr__(self, "group_id", _required(self.group_id, "group_id", maximum=120))
        object.__setattr__(self, "member_id", _required(self.member_id, "member_id", maximum=120))
        object.__setattr__(self, "text", _required(self.text, "text", maximum=500))
        object.__setattr__(self, "occurred_at", max(0, int(self.occurred_at)))
        object.__setattr__(self, "scene_type", _required(self.scene_type, "scene_type", maximum=40))
        object.__setattr__(self, "short_reaction", bool(self.short_reaction))


@dataclass(frozen=True)
class MemberStyleMaturity:
    eligible_message_count: int
    substantive_message_count: int
    active_day_count: int
    scene_types: tuple[str, ...]
    ready: bool

    @classmethod
    def from_evidence(
        cls, evidence: tuple[MemberStyleEvidence, ...]
    ) -> "MemberStyleMaturity":
        unique = {item.event_id: item for item in evidence}
        values = tuple(unique.values())
        count = len(values)
        substantive = sum(not item.short_reaction for item in values)
        days = {
            datetime.fromtimestamp(item.occurred_at, tz=timezone.utc).date()
            for item in values
        }
        scenes = tuple(sorted({item.scene_type for item in values}))
        ready = bool(
            count >= 40
            and substantive >= 32
            and substantive * 4 >= count * 3
            and len(days) >= 5
            and len(scenes) >= 3
        )
        return cls(count, substantive, len(days), scenes, ready)


@dataclass(frozen=True)
class MemberSpeechStyle:
    group_id: str
    member_id: str
    version: int
    status: str
    opening_patterns: tuple[str, ...]
    progression_patterns: tuple[str, ...]
    closing_patterns: tuple[str, ...]
    length_rhythm: str
    directness: str
    disagreement_style: str
    play_style: str
    care_style: str
    addressing_style: str
    particles_punctuation: str
    stable_traits: tuple[str, ...]
    occasional_traits: tuple[str, ...]
    evidence_event_ids: tuple[str, ...]
    eligible_message_count: int
    active_day_count: int
    scene_types: tuple[str, ...]
    generated_at: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "group_id", _required(self.group_id, "group_id", maximum=120))
        object.__setattr__(self, "member_id", _required(self.member_id, "member_id", maximum=120))
        if int(self.version) < 1:
            raise ValueError("style version must be positive")
        object.__setattr__(self, "version", int(self.version))
        status = str(self.status or "").upper()
        if status not in _STYLE_STATUSES:
            raise ValueError("unknown member style status")
        object.__setattr__(self, "status", status)
        for name in (
            "opening_patterns",
            "progression_patterns",
            "closing_patterns",
            "stable_traits",
            "occasional_traits",
            "evidence_event_ids",
            "scene_types",
        ):
            maximum = 32 if name == "evidence_event_ids" else 12
            object.__setattr__(
                self,
                name,
                _bounded_tuple(tuple(getattr(self, name)), name, maximum_items=maximum),
            )
        for name in (
            "length_rhythm",
            "directness",
            "disagreement_style",
            "play_style",
            "care_style",
            "addressing_style",
            "particles_punctuation",
        ):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        for name in ("eligible_message_count", "active_day_count", "generated_at"):
            object.__setattr__(self, name, max(0, int(getattr(self, name))))


@dataclass(frozen=True)
class ImitationSession:
    session_id: str
    group_id: str
    target_member_id: str
    target_display_name: str
    style_version: int
    started_by_admin_id: str
    started_at: int
    expires_at: int
    stopped_at: int | None = None
    stopped_by: str | None = None
    stop_reason: str | None = None


class MemberStyleEvidencePolicy:
    """Select authored, privacy-safe text without interpreting a personality."""

    def select(self, observation) -> MemberStyleEvidence | None:
        payload = observation.payload
        text = " ".join(str(payload.get("text") or "").split())
        if not text or len(text) > 500:
            return None
        if payload.get("social_eligible") is False:
            return None
        if str(payload.get("interaction_owner") or "").upper() == "EXTERNAL_PLUGIN":
            return None
        if payload.get("chorus_chain_id") or payload.get("is_chorus"):
            return None
        segments = payload.get("segments")
        if isinstance(segments, (list, tuple)) and any(
            isinstance(item, Mapping)
            and str(item.get("type") or "").lower() in {"forward", "node", "json", "xml"}
            for item in segments
        ):
            return None
        if text.startswith(_COMMAND_PREFIXES):
            return None
        if any(pattern.search(text) for pattern in _SENSITIVE_PATTERNS):
            return None
        if _ATTACK_PATTERNS.search(text):
            return None
        return MemberStyleEvidence(
            event_id=observation.event_id,
            group_id=observation.group_id,
            member_id=observation.actor_id,
            text=text,
            occurred_at=observation.occurred_at,
            scene_type=self._scene_type(text),
            short_reaction=len(text) <= 4,
        )

    @staticmethod
    def _scene_type(text: str) -> str:
        if any(token in text for token in ("哈哈", "笑死", "绷不住", "乐")):
            return "banter"
        if any(token in text for token in ("休息", "喝水", "吃饭", "别熬", "没事吧")):
            return "care"
        if text.endswith(("?", "？")) or any(
            token in text for token in ("怎么", "为什么", "能不能", "是不是")
        ):
            return "question"
        if any(token in text for token in ("不对", "不是", "但是", "可问题是")):
            return "disagreement"
        return "statement"


__all__ = (
    "ImitationSession",
    "MemberSpeechStyle",
    "MemberStyleEvidence",
    "MemberStyleEvidencePolicy",
    "MemberStyleMaturity",
    "MemberStyleSetting",
)
