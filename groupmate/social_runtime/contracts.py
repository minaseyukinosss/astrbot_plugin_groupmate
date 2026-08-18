"""Stable domain contracts shared by the Social Runtime v2 core."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class RuntimeMode(str, Enum):
    OFF = "OFF"
    SHADOW = "SHADOW"
    SOCIAL_RUNTIME = "SOCIAL_RUNTIME"


@dataclass(frozen=True)
class SocialEventEnvelope:
    event_id: str
    event_type: str
    occurred_at: int
    received_at: int
    persona_id: str
    group_id: str | None
    actor_id: str | None
    source_message_id: str | None
    correlation_id: str
    causation_id: str | None
    payload: Mapping[str, object]

    @classmethod
    def create(cls, **values) -> "SocialEventEnvelope":
        normalized = dict(values)
        for field in ("event_id", "event_type", "persona_id", "correlation_id"):
            value = str(normalized.get(field) or "").strip()
            if not value:
                raise ValueError(f"{field} must not be empty")
            normalized[field] = value
        for field in ("occurred_at", "received_at"):
            value = int(normalized.get(field, -1))
            if value < 0:
                raise ValueError(f"{field} must not be negative")
            normalized[field] = value
        payload = dict(normalized.get("payload") or {})
        try:
            json.dumps(payload, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("payload must be JSON serializable") from exc
        normalized["payload"] = MappingProxyType(payload)
        return cls(**normalized)

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "received_at": self.received_at,
            "persona_id": self.persona_id,
            "group_id": self.group_id,
            "actor_id": self.actor_id,
            "source_message_id": self.source_message_id,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, values: Mapping[str, object]) -> "SocialEventEnvelope":
        return cls.create(**dict(values))


@dataclass(frozen=True)
class ActorCursor:
    actor_key: str
    last_sequence: int
    version: int


@dataclass(frozen=True)
class PersonaSnapshot:
    persona_id: str
    state_version: int
    config_version: int
    presence: str
    energy: int
    mode: str
    modifiers: tuple[str, ...]


@dataclass(frozen=True)
class GlobalStateEffect:
    effect_id: str
    source_event_id: str
    expected_version: int
    kind: str
    amount: int
    evidence_event_ids: tuple[str, ...]
