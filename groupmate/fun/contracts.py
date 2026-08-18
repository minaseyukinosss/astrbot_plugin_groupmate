"""Shared contracts for optional fun features.

Fun features are low-frequency, opt-in experience additions. They may affect
presentation surfaces such as the bot's own group card, but they must not send
chat messages, write long-term social memory, or participate in reply ownership.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

try:
    from typing import Protocol
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol

from ..models import ChatMessage


def _clean_text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[: max(0, int(limit))]


def _clean_id(value: Any, limit: int = 80) -> str:
    return str(value or "").strip()[: max(0, int(limit))]


@dataclass(frozen=True)
class FunParticipant:
    user_id: str
    display_name: str = ""
    role: str = "involved"
    confidence: float = 0.0
    visibility: str = "private"

    def __post_init__(self) -> None:
        confidence = max(0.0, min(1.0, float(self.confidence or 0.0)))
        visibility = _clean_id(self.visibility or "private", 20) or "private"
        if visibility not in {"private", "self", "role"}:
            visibility = "private"
        object.__setattr__(self, "user_id", _clean_id(self.user_id, 80))
        object.__setattr__(self, "display_name", _clean_text(self.display_name, 80))
        object.__setattr__(self, "role", _clean_id(self.role or "involved", 40))
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "visibility", visibility)


@dataclass(frozen=True)
class FunFeatureContext:
    persona_id: str
    group_id: str
    now: int
    paused: bool = False
    recent_messages: Tuple[ChatMessage, ...] = ()
    active_event: Optional["FunFeatureEvent"] = None
    force: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "persona_id", _clean_id(self.persona_id, 80))
        object.__setattr__(self, "group_id", _clean_id(self.group_id, 80))
        object.__setattr__(self, "now", int(self.now or 0))
        object.__setattr__(self, "paused", bool(self.paused))
        object.__setattr__(self, "recent_messages", tuple(self.recent_messages or ()))
        object.__setattr__(self, "force", bool(self.force))


@dataclass(frozen=True)
class FunFeaturePlan:
    feature_id: str
    group_id: str
    action_kind: str
    public_value: str
    private_context: Mapping[str, Any] = field(default_factory=dict)
    participants: Tuple[FunParticipant, ...] = ()
    expires_at: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "feature_id", _clean_id(self.feature_id, 80))
        object.__setattr__(self, "group_id", _clean_id(self.group_id, 80))
        object.__setattr__(self, "action_kind", _clean_id(self.action_kind, 60))
        object.__setattr__(self, "public_value", _clean_text(self.public_value, 80))
        object.__setattr__(self, "private_context", dict(self.private_context or {}))
        object.__setattr__(self, "participants", tuple(self.participants or ()))
        object.__setattr__(self, "expires_at", int(self.expires_at or 0))


@dataclass(frozen=True)
class FunFeatureEvent:
    event_id: str
    feature_id: str
    persona_id: str
    group_id: str
    action_kind: str
    public_value: str
    private_context: Dict[str, Any] = field(default_factory=dict)
    participants: Tuple[FunParticipant, ...] = ()
    created_at: int = 0
    expires_at: int = 0
    status: str = "active"
    error_code: str = ""

    def __post_init__(self) -> None:
        status = _clean_id(self.status or "active", 20) or "active"
        if status not in {"active", "failed", "restored", "expired"}:
            status = "failed"
        object.__setattr__(self, "event_id", _clean_id(self.event_id, 80))
        object.__setattr__(self, "feature_id", _clean_id(self.feature_id, 80))
        object.__setattr__(self, "persona_id", _clean_id(self.persona_id, 80))
        object.__setattr__(self, "group_id", _clean_id(self.group_id, 80))
        object.__setattr__(self, "action_kind", _clean_id(self.action_kind, 60))
        object.__setattr__(self, "public_value", _clean_text(self.public_value, 80))
        object.__setattr__(self, "private_context", dict(self.private_context or {}))
        object.__setattr__(self, "participants", tuple(self.participants or ()))
        object.__setattr__(self, "created_at", int(self.created_at or 0))
        object.__setattr__(self, "expires_at", int(self.expires_at or 0))
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "error_code", _clean_id(self.error_code, 80))


class FunActionPort(Protocol):
    async def set_own_group_card(self, group_id: str, card: str) -> str:
        """Return empty string on success, or a stable error code."""
        ...


class FunFeature(Protocol):
    feature_id: str

    def due(self, context: FunFeatureContext) -> bool:
        ...

    def plan(self, context: FunFeatureContext) -> Optional[FunFeaturePlan]:
        ...

    async def apply(
        self,
        plan: FunFeaturePlan,
        actions: FunActionPort,
    ) -> str:
        """Return empty string on success, or a stable error code."""
        ...


def participants_to_json(participants: Sequence[FunParticipant]) -> Tuple[Dict[str, Any], ...]:
    return tuple(
        {
            "user_id": item.user_id,
            "display_name": item.display_name,
            "role": item.role,
            "confidence": item.confidence,
            "visibility": item.visibility,
        }
        for item in tuple(participants or ())
    )


def participants_from_json(raw: Any) -> Tuple[FunParticipant, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return ()
    result = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        result.append(
            FunParticipant(
                user_id=str(item.get("user_id") or ""),
                display_name=str(item.get("display_name") or ""),
                role=str(item.get("role") or "involved"),
                confidence=float(item.get("confidence") or 0.0),
                visibility=str(item.get("visibility") or "private"),
            )
        )
    return tuple(part for part in result if part.user_id)
