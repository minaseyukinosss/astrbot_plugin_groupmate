"""Frozen contracts for member cognition."""

from __future__ import annotations

import math
from dataclasses import dataclass


def _required(value: object, label: str, *, maximum: int = 160) -> str:
    normalized = " ".join(str(value or "").split())
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{label} must contain 1-{maximum} characters")
    return normalized


def _scope(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(str(item or "").strip() for item in values)
    if any(not item for item in normalized):
        raise ValueError("scope values must not be empty")
    return normalized


def _unit(value: object, label: str) -> float:
    normalized = float(value)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{label} must be between 0 and 1")
    return normalized


def _identifiers(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    normalized = tuple(
        dict.fromkeys(_required(item, label, maximum=180) for item in values)
    )
    if not normalized or len(normalized) > 16:
        raise ValueError(f"{label} must contain 1-16 values")
    return normalized


@dataclass(frozen=True)
class MemberIdentity:
    persona_id: str
    platform: str
    actor_id: str
    display_name: str
    updated_at: int
    avatar_ref: str | None = None
    system_roles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        persona_id, platform, actor_id = _scope(
            (self.persona_id, self.platform, self.actor_id)
        )
        object.__setattr__(self, "persona_id", persona_id)
        object.__setattr__(self, "platform", platform)
        object.__setattr__(self, "actor_id", actor_id)
        object.__setattr__(
            self, "display_name", _required(self.display_name, "display_name", maximum=48)
        )
        object.__setattr__(self, "updated_at", max(0, int(self.updated_at)))
        object.__setattr__(
            self,
            "system_roles",
            tuple(dict.fromkeys(_required(item, "system_role", maximum=40) for item in self.system_roles)),
        )


@dataclass(frozen=True)
class MemberAlias:
    persona_id: str
    group_id: str
    actor_id: str
    alias: str
    alias_type: str
    confidence: float
    first_seen_at: int
    last_seen_at: int
    source_event_id: str | None = None
    status: str = "confirmed"


@dataclass(frozen=True)
class ProfileObservation:
    event_id: str
    persona_id: str
    group_id: str
    actor_id: str
    payload: dict[str, object]
    occurred_at: int
    status: str = "pending"
    attempt: int = 0
    next_attempt_at: int = 0
    diagnostic_code: str | None = None


@dataclass(frozen=True)
class ProfileFact:
    fact_id: str
    persona_id: str
    group_id: str
    subject_id: str
    category: str
    summary: str
    source_kind: str
    source_actor_id: str
    source_event_ids: tuple[str, ...]
    confidence: float
    status: str
    evidence_count: int
    valid_from: int
    valid_until: int | None = None
    supersedes_fact_id: str | None = None
    injectable: bool = False

    def __post_init__(self) -> None:
        values = _scope(
            (self.fact_id, self.persona_id, self.group_id, self.subject_id)
        )
        for field, value in zip(
            ("fact_id", "persona_id", "group_id", "subject_id"), values
        ):
            object.__setattr__(self, field, value)
        object.__setattr__(self, "category", _required(self.category, "category", maximum=40))
        object.__setattr__(self, "summary", _required(self.summary, "summary"))
        object.__setattr__(
            self, "source_kind", _required(self.source_kind, "source_kind", maximum=40)
        )
        object.__setattr__(
            self,
            "source_actor_id",
            _required(self.source_actor_id, "source_actor_id", maximum=120),
        )
        object.__setattr__(
            self,
            "source_event_ids",
            _identifiers(self.source_event_ids, "source_event_id"),
        )
        object.__setattr__(self, "confidence", _unit(self.confidence, "confidence"))
        object.__setattr__(self, "status", _required(self.status, "status", maximum=24))
        object.__setattr__(self, "evidence_count", max(1, int(self.evidence_count)))
        object.__setattr__(self, "valid_from", max(0, int(self.valid_from)))


@dataclass(frozen=True)
class ProfileFactCandidate:
    candidate_id: str
    persona_id: str
    group_id: str
    subject_id: str
    category: str
    summary: str
    source_kind: str
    source_actor_id: str
    source_event_ids: tuple[str, ...]
    confidence: float
    evidence_count: int
    observed_at: int

    def __post_init__(self) -> None:
        values = _scope(
            (
                self.candidate_id,
                self.persona_id,
                self.group_id,
                self.subject_id,
            )
        )
        for field, value in zip(
            ("candidate_id", "persona_id", "group_id", "subject_id"), values
        ):
            object.__setattr__(self, field, value)
        object.__setattr__(self, "category", _required(self.category, "category", maximum=40))
        object.__setattr__(self, "summary", _required(self.summary, "summary"))
        object.__setattr__(
            self, "source_kind", _required(self.source_kind, "source_kind", maximum=40)
        )
        object.__setattr__(
            self,
            "source_actor_id",
            _required(self.source_actor_id, "source_actor_id", maximum=120),
        )
        object.__setattr__(
            self,
            "source_event_ids",
            _identifiers(self.source_event_ids, "source_event_id"),
        )
        object.__setattr__(self, "confidence", _unit(self.confidence, "confidence"))
        object.__setattr__(self, "evidence_count", max(1, int(self.evidence_count)))
        object.__setattr__(self, "observed_at", max(0, int(self.observed_at)))


@dataclass(frozen=True)
class ProfileEpisode:
    episode_id: str
    persona_id: str
    group_id: str
    title: str
    summary: str
    participants: tuple[str, ...]
    source_event_ids: tuple[str, ...]
    episode_type: str
    valence: float
    importance: float
    confidence: float
    status: str
    occurred_at: int
    last_reinforced_at: int


@dataclass(frozen=True)
class SocialEdge:
    edge_id: str
    persona_id: str
    group_id: str
    source_member_id: str
    target_member_id: str
    relation_type: str
    direction: str
    strength: float
    confidence: float
    source_event_ids: tuple[str, ...]
    status: str
    valid_from: int
    valid_until: int | None
    last_observed_at: int


@dataclass(frozen=True)
class SocialEdgeCandidate:
    candidate_id: str
    persona_id: str
    group_id: str
    source_member_id: str
    target_member_id: str
    relation_type: str
    direction: str
    strength: float
    confidence: float
    source_event_ids: tuple[str, ...]
    observed_at: int


@dataclass(frozen=True)
class ProfileKnownCognition:
    facts: tuple[ProfileFact, ...] = ()
    episodes: tuple[ProfileEpisode, ...] = ()
    edges: tuple[SocialEdge, ...] = ()


@dataclass(frozen=True)
class ProfileCorrection:
    old: ProfileFact
    new: ProfileFact


@dataclass(frozen=True)
class ProfileSnapshot:
    persona_id: str
    group_id: str
    subject_id: str
    one_line_portrait: str
    group_roles: tuple[str, ...]
    individual_fingerprints: tuple[str, ...]
    preferences_and_boundaries: tuple[str, ...]
    representative_episode_ids: tuple[str, ...]
    relationship_summary: str
    maturity: str
    source_revision: int
    generated_at: int


__all__ = (
    "MemberAlias",
    "MemberIdentity",
    "ProfileEpisode",
    "ProfileFact",
    "ProfileFactCandidate",
    "ProfileCorrection",
    "ProfileKnownCognition",
    "ProfileObservation",
    "ProfileSnapshot",
    "SocialEdge",
    "SocialEdgeCandidate",
)
