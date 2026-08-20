"""Stable ground-truth contracts for Social Runtime evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


def _label_text(value: object, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def _label_items(values: object, field: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise ValueError(f"{field} must be a sequence")
    normalized = tuple(_label_text(value, field) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field} must not contain duplicates")
    return normalized


@dataclass(frozen=True)
class EvaluationLabel:
    attention: bool
    action: bool
    target: str | None
    acceptable_intents: tuple[str, ...]
    unacceptable_intents: tuple[str, ...]
    modalities: tuple[str, ...]
    sensitivity: str
    expires_after_ms: int

    @classmethod
    def create(cls, **values: object) -> "EvaluationLabel":
        attention = values.get("attention")
        if type(attention) is not bool:
            raise ValueError("attention must be a boolean")
        action = values.get("action")
        if type(action) is not bool:
            raise ValueError("action must be a boolean")

        acceptable = _label_items(
            values.get("acceptable_intents", ()), "acceptable_intents"
        )
        unacceptable = _label_items(
            values.get("unacceptable_intents", ()), "unacceptable_intents"
        )
        if set(acceptable) & set(unacceptable):
            raise ValueError("acceptable and unacceptable intents must be disjoint")

        expires_after_ms = values.get("expires_after_ms")
        if type(expires_after_ms) is not int:
            raise ValueError("expires_after_ms must be an integer")
        if expires_after_ms < 0:
            raise ValueError("expires_after_ms must not be negative")

        return cls(
            attention=attention,
            action=action,
            target=_label_text(values.get("target"), "target", optional=True),
            acceptable_intents=acceptable,
            unacceptable_intents=unacceptable,
            modalities=_label_items(values.get("modalities", ()), "modalities"),
            sensitivity=str(_label_text(values.get("sensitivity"), "sensitivity")),
            expires_after_ms=expires_after_ms,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "attention": self.attention,
            "action": self.action,
            "target": self.target,
            "acceptable_intents": list(self.acceptable_intents),
            "unacceptable_intents": list(self.unacceptable_intents),
            "modalities": list(self.modalities),
            "sensitivity": self.sensitivity,
            "expires_after_ms": self.expires_after_ms,
        }

    @classmethod
    def from_dict(cls, values: Mapping[str, object]) -> "EvaluationLabel":
        return cls.create(**dict(values))


__all__ = ("EvaluationLabel",)
