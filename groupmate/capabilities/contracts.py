"""Immutable data exchanged across the capability execution boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from ..models import StringEnum


_CAPABILITY_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")


class CapabilityStatus(StringEnum):
    SUCCESS = "success"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    HANDOFF = "handoff"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class CapabilityRequest:
    capability_name: str
    message_text: str = ""
    input_facts: Tuple[str, ...] = ()
    media_locators: Tuple[str, ...] = ()
    group_id: str = ""
    actor_id: str = ""
    message_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "capability_name", validate_capability_name(self.capability_name)
        )
        object.__setattr__(self, "message_text", _clean_text(self.message_text))
        object.__setattr__(self, "input_facts", _clean_texts(self.input_facts))
        object.__setattr__(
            self,
            "media_locators",
            tuple(
                locator
                for locator in (
                    str(value or "").strip() for value in (self.media_locators or ())
                )
                if locator
            ),
        )
        object.__setattr__(self, "group_id", _clean_identifier(self.group_id))
        object.__setattr__(self, "actor_id", _clean_identifier(self.actor_id))
        object.__setattr__(self, "message_id", _clean_identifier(self.message_id))


@dataclass(frozen=True)
class MediaCandidate:
    source: str
    locator: str
    media_kind: str
    semantic_label: str
    purpose: str = ""
    safety_label: str = ""

    def __post_init__(self) -> None:
        for field_name in (
            "source",
            "locator",
            "media_kind",
            "semantic_label",
            "purpose",
            "safety_label",
        ):
            object.__setattr__(
                self, field_name, _clean_text(getattr(self, field_name))
            )
        if not self.source or not self.locator or not self.media_kind:
            raise ValueError("media candidate source, locator, and kind are required")
        if not self.semantic_label:
            raise ValueError("media candidate semantic_label is required")
        if not self.purpose:
            raise ValueError("media candidate purpose is required")
        if not self.safety_label:
            raise ValueError("media candidate safety_label is required")


@dataclass(frozen=True)
class CapabilityResult:
    status: CapabilityStatus
    capability_name: str
    facts: Tuple[str, ...] = ()
    user_text: str = ""
    error_code: str = ""
    diagnostic: str = ""
    media_candidates: Tuple[MediaCandidate, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, CapabilityStatus):
            raise TypeError("status must be a CapabilityStatus")
        object.__setattr__(
            self, "capability_name", validate_capability_name(self.capability_name)
        )
        facts = _clean_texts(self.facts)
        candidates = tuple(self.media_candidates or ())
        if not all(isinstance(item, MediaCandidate) for item in candidates):
            raise TypeError("media_candidates must contain MediaCandidate values")
        if self.status is not CapabilityStatus.SUCCESS and (facts or candidates):
            raise ValueError(
                "non-success capability results cannot contain completed facts or media"
            )
        object.__setattr__(self, "facts", facts)
        object.__setattr__(self, "user_text", _clean_text(self.user_text))
        object.__setattr__(self, "error_code", _clean_identifier(self.error_code))
        object.__setattr__(self, "diagnostic", _clean_text(self.diagnostic))
        object.__setattr__(self, "media_candidates", candidates)


def validate_capability_name(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("capability_name must be a string")
    name = value.strip()
    if not _CAPABILITY_NAME.match(name):
        raise ValueError("capability_name must be a stable lowercase identifier")
    return name


def _clean_texts(values: Optional[Sequence[object]]) -> Tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError("text collections must be sequences, not strings")
    return tuple(
        cleaned
        for cleaned in (_clean_text(value) for value in (values or ()))
        if cleaned
    )


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _clean_identifier(value: object) -> str:
    return str(value or "").strip()
