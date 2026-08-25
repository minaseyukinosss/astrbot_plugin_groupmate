"""Versioned Persona facts separated from stable behavior rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


_FACT_CATEGORIES = frozenset(
    {
        "current_state",
        "history",
        "abilities",
        "daily_life",
        "values",
        "avoidances",
    }
)


@dataclass(frozen=True)
class PersonaFact:
    fact_id: str
    category: str
    text: str
    canon_phase: str
    valid_from: int
    valid_to: int | None
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("fact_id", "category", "text", "canon_phase"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError("persona fact fields must not be empty")
            object.__setattr__(self, name, value)
        if self.category not in _FACT_CATEGORIES:
            raise ValueError("unknown persona fact category")
        valid_from = int(self.valid_from)
        valid_to = None if self.valid_to is None else int(self.valid_to)
        if valid_from < 0 or (valid_to is not None and valid_to < valid_from):
            raise ValueError("persona fact validity range is invalid")
        object.__setattr__(self, "valid_from", valid_from)
        object.__setattr__(self, "valid_to", valid_to)
        tags = tuple(
            dict.fromkeys(
                str(item or "").strip()
                for item in self.tags
                if str(item or "").strip()
            )
        )
        if len(tags) > 12:
            raise ValueError("persona fact supports at most 12 tags")
        object.__setattr__(self, "tags", tags)

    def active_at(self, phase: int) -> bool:
        resolved = int(phase)
        return self.valid_from <= resolved and (
            self.valid_to is None or resolved <= self.valid_to
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "fact_id": self.fact_id,
            "category": self.category,
            "text": self.text,
            "canon_phase": self.canon_phase,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "tags": list(self.tags),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "PersonaFact":
        if not isinstance(value, Mapping):
            raise ValueError("persona fact must be an object")
        return cls(
            fact_id=str(value.get("fact_id") or ""),
            category=str(value.get("category") or ""),
            text=str(value.get("text") or ""),
            canon_phase=str(value.get("canon_phase") or ""),
            valid_from=int(value.get("valid_from", -1)),
            valid_to=(
                None
                if value.get("valid_to") is None
                else int(value["valid_to"])
            ),
            tags=tuple(value.get("tags", ())),
        )


@dataclass(frozen=True)
class PersonaCanonSnapshot:
    current_state: tuple[PersonaFact, ...]
    history: tuple[PersonaFact, ...]
    abilities: tuple[PersonaFact, ...]
    daily_life: tuple[PersonaFact, ...]
    values: tuple[PersonaFact, ...]
    avoidances: tuple[str, ...]


@dataclass(frozen=True)
class PersonaCanon:
    current_phase: int
    checked_at: int
    facts: tuple[PersonaFact, ...]

    def __post_init__(self) -> None:
        current_phase = int(self.current_phase)
        checked_at = int(self.checked_at)
        if current_phase < 0 or checked_at < 0:
            raise ValueError("persona canon versions must not be negative")
        facts = tuple(self.facts)
        ids = tuple(item.fact_id for item in facts)
        if len(ids) != len(set(ids)):
            raise ValueError("persona fact_id must be unique")
        object.__setattr__(self, "current_phase", current_phase)
        object.__setattr__(self, "checked_at", checked_at)
        object.__setattr__(self, "facts", facts)

    @classmethod
    def empty(cls) -> "PersonaCanon":
        return cls(current_phase=0, checked_at=0, facts=())

    def current_snapshot(self) -> PersonaCanonSnapshot:
        active = tuple(
            item for item in self.facts if item.active_at(self.current_phase)
        )
        return PersonaCanonSnapshot(
            current_state=tuple(
                item for item in active if item.category == "current_state"
            ),
            history=tuple(
                item for item in self.facts if item.category == "history"
            ),
            abilities=tuple(
                item for item in active if item.category == "abilities"
            ),
            daily_life=tuple(
                item for item in active if item.category == "daily_life"
            ),
            values=tuple(item for item in active if item.category == "values"),
            avoidances=tuple(
                item.text for item in active if item.category == "avoidances"
            ),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "current_phase": self.current_phase,
            "checked_at": self.checked_at,
            "facts": [item.to_mapping() for item in self.facts],
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object] | None) -> "PersonaCanon":
        if value is None:
            return cls.empty()
        if not isinstance(value, Mapping):
            raise ValueError("persona canon must be an object")
        raw_facts = value.get("facts", ())
        if not isinstance(raw_facts, (list, tuple)):
            raise ValueError("persona canon facts must be a list")
        return cls(
            current_phase=int(value.get("current_phase", 0)),
            checked_at=int(value.get("checked_at", 0)),
            facts=tuple(PersonaFact.from_mapping(item) for item in raw_facts),
        )


__all__ = ("PersonaCanon", "PersonaCanonSnapshot", "PersonaFact")
