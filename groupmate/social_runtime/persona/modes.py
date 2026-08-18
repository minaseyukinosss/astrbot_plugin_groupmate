"""Deterministic mode director driven only by authoritative signals."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping


class InvalidModeCombination(ValueError):
    """Raised when a primary mode and modifier combination is incoherent."""


_PRIMARY_MODES = {"social", "focused_task", "quiet_observer", "boundary"}
_MODIFIERS = {"playful", "warm", "drowsy", "irritated"}


@dataclass(frozen=True)
class PersonaModeState:
    primary: str
    modifiers: tuple[str, ...]
    activated_by: tuple[str, ...]
    expires_at: int | None

    def __post_init__(self) -> None:
        if self.primary not in _PRIMARY_MODES:
            raise InvalidModeCombination(f"unsupported primary mode: {self.primary}")
        if len(set(self.modifiers)) != len(self.modifiers):
            raise InvalidModeCombination("mode modifiers must be unique")
        unknown = set(self.modifiers) - _MODIFIERS
        if unknown:
            raise InvalidModeCombination(f"unsupported mode modifiers: {sorted(unknown)}")
        if self.primary == "boundary" and "playful" in self.modifiers:
            raise InvalidModeCombination("boundary mode cannot be playful")

    @classmethod
    def social(cls) -> "PersonaModeState":
        return cls("social", (), (), None)


@dataclass(frozen=True)
class ModeSignal:
    kind: str
    source_id: str
    occurred_at: int
    value: str | None = None
    expires_at: int | None = None


class ModeDirector:
    def transition(
        self, current: PersonaModeState, signal: ModeSignal
    ) -> PersonaModeState:
        if not signal.source_id.strip():
            raise ValueError("mode signal requires source_id")
        primary = current.primary
        modifiers = current.modifiers

        if signal.kind == "task.accepted":
            primary = "focused_task"
        elif signal.kind == "task.completed":
            primary = "social"
        elif signal.kind == "time.drowsy":
            modifiers = self._add(modifiers, "drowsy")
        elif signal.kind == "time.awake":
            modifiers = self._remove(modifiers, "drowsy")
        elif signal.kind == "load.high":
            primary = "quiet_observer"
        elif signal.kind == "boundary.enter":
            primary = "boundary"
            modifiers = self._remove(modifiers, "playful")
        elif signal.kind == "boundary.clear":
            primary = "social"
        elif signal.kind == "social.playful":
            modifiers = self._add(modifiers, "playful")
        elif signal.kind == "admin.mode":
            if signal.value not in _PRIMARY_MODES:
                raise ValueError("admin mode signal requires a valid primary mode")
            primary = str(signal.value)
        else:
            raise ValueError(f"unsupported mode signal: {signal.kind}")

        reasons = current.activated_by
        if signal.source_id not in reasons:
            reasons += (signal.source_id,)
        return PersonaModeState(
            primary=primary,
            modifiers=modifiers,
            activated_by=reasons,
            expires_at=signal.expires_at,
        )

    @staticmethod
    def to_dict(state: PersonaModeState) -> dict[str, object]:
        return asdict(state)

    @staticmethod
    def from_dict(payload: Mapping[str, object]) -> PersonaModeState:
        return PersonaModeState(
            primary=str(payload["primary"]),
            modifiers=tuple(payload["modifiers"]),
            activated_by=tuple(payload["activated_by"]),
            expires_at=payload.get("expires_at"),
        )

    @staticmethod
    def _add(values: tuple[str, ...], value: str) -> tuple[str, ...]:
        return values if value in values else values + (value,)

    @staticmethod
    def _remove(values: tuple[str, ...], value: str) -> tuple[str, ...]:
        return tuple(item for item in values if item != value)


__all__ = (
    "InvalidModeCombination",
    "ModeDirector",
    "ModeSignal",
    "PersonaModeState",
)
