"""Immutable data exchanged across the capability execution boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

from ..models import StringEnum


_CAPABILITY_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
_MEDIA_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")


class CapabilityStatus(StringEnum):
    SUCCESS = "success"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    HANDOFF = "handoff"
    TIMEOUT = "timeout"


class CapabilityPermission(StringEnum):
    VISION_READ = "vision.read"
    EXTERNAL_HANDOFF = "external.handoff"
    MEDIA_RESULT = "media.result"


class CapabilityLatencyClass(StringEnum):
    INLINE = "inline"
    INTERACTIVE = "interactive"
    BACKGROUND = "background"


class CapabilityCostClass(StringEnum):
    FREE = "free"
    METERED = "metered"
    EXPENSIVE = "expensive"


class CapabilityFailurePolicy(StringEnum):
    FAIL_CLOSED = "fail_closed"
    CLARIFY = "clarify"
    HANDOFF = "handoff"


@dataclass(frozen=True)
class CapabilityMediaPolicy:
    capability_media_allowed: bool = False
    allowed_media_kinds: Tuple[str, ...] = ()
    allowed_safety_labels: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "capability_media_allowed",
            bool(self.capability_media_allowed),
        )
        object.__setattr__(
            self,
            "allowed_media_kinds",
            _clean_texts(self.allowed_media_kinds),
        )
        object.__setattr__(
            self,
            "allowed_safety_labels",
            _clean_texts(self.allowed_safety_labels),
        )


@dataclass(frozen=True)
class CapabilityManifest:
    name: str
    version: str
    supported_intents: Tuple[str, ...] = ()
    permission_profile: Tuple[CapabilityPermission, ...] = ()
    latency_class: CapabilityLatencyClass = CapabilityLatencyClass.INTERACTIVE
    cost_class: CapabilityCostClass = CapabilityCostClass.FREE
    failure_policy: CapabilityFailurePolicy = CapabilityFailurePolicy.FAIL_CLOSED
    max_result_size: int = 2048
    default_timeout_seconds: float = 10.0
    max_concurrency: int = 1

    def __post_init__(self) -> None:
        permissions = tuple(self.permission_profile or ())
        if not permissions:
            raise ValueError(
                "permission_profile must declare at least one permission"
            )
        if not all(isinstance(item, CapabilityPermission) for item in permissions):
            raise TypeError(
                "permission_profile must contain CapabilityPermission values"
            )
        latency = self.latency_class
        if not isinstance(latency, CapabilityLatencyClass):
            latency = CapabilityLatencyClass(str(latency))
        cost = self.cost_class
        if not isinstance(cost, CapabilityCostClass):
            cost = CapabilityCostClass(str(cost))
        failure = self.failure_policy
        if not isinstance(failure, CapabilityFailurePolicy):
            failure = CapabilityFailurePolicy(str(failure))
        max_size = int(self.max_result_size)
        timeout = float(self.default_timeout_seconds)
        concurrency = int(self.max_concurrency)
        if max_size <= 0:
            raise ValueError("max_result_size must be positive")
        if timeout <= 0:
            raise ValueError("default_timeout_seconds must be positive")
        if concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        version = _clean_identifier(self.version)
        if not version:
            raise ValueError("capability manifest version is required")

        object.__setattr__(self, "name", validate_capability_name(self.name))
        object.__setattr__(self, "version", version)
        object.__setattr__(
            self,
            "supported_intents",
            _clean_texts(self.supported_intents),
        )
        object.__setattr__(self, "permission_profile", permissions)
        object.__setattr__(self, "latency_class", latency)
        object.__setattr__(self, "cost_class", cost)
        object.__setattr__(self, "failure_policy", failure)
        object.__setattr__(self, "max_result_size", max_size)
        object.__setattr__(self, "default_timeout_seconds", timeout)
        object.__setattr__(self, "max_concurrency", concurrency)


@dataclass(frozen=True)
class CapabilityContext:
    persona_id: str
    group_id: str
    actor_id: str
    message_id: str
    trace_id: str
    deadline_at: int
    allowed_permissions: Tuple[CapabilityPermission, ...] = ()
    media_policy: CapabilityMediaPolicy = field(
        default_factory=CapabilityMediaPolicy
    )

    def __post_init__(self) -> None:
        for field_name in (
            "persona_id",
            "group_id",
            "actor_id",
            "message_id",
            "trace_id",
        ):
            value = _clean_identifier(getattr(self, field_name))
            if field_name in ("persona_id", "group_id", "trace_id") and not value:
                raise ValueError("{} is required".format(field_name))
            object.__setattr__(self, field_name, value)
        permissions = tuple(self.allowed_permissions or ())
        if not all(isinstance(item, CapabilityPermission) for item in permissions):
            raise TypeError(
                "allowed_permissions must contain CapabilityPermission values"
            )
        if not isinstance(self.media_policy, CapabilityMediaPolicy):
            raise TypeError("media_policy must be a CapabilityMediaPolicy")
        object.__setattr__(self, "deadline_at", int(self.deadline_at))
        object.__setattr__(self, "allowed_permissions", permissions)


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
    media_id: str
    source: str
    locator: str
    media_kind: str
    semantic_label: str
    purpose: str = ""
    safety_label: str = ""

    def __post_init__(self) -> None:
        for field_name in (
            "media_id",
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
        if not _MEDIA_ID.match(self.media_id):
            raise ValueError("media candidate media_id must be a stable identifier")
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
