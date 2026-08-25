"""Evidence-derived, group-scoped multidimensional relationships."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Mapping


@dataclass(frozen=True)
class RelationshipEvidence:
    event_id: str
    kind: str
    amount: int
    occurred_at: int


@dataclass(frozen=True)
class RelationshipProjection:
    persona_id: str
    group_id: str
    subject_id: str
    familiarity: int = 0
    warmth: int = 0
    trust: int = 0
    reciprocity: int = 0
    play_acceptance: int = 0
    reliability: int = 0
    care_permission: int = 0
    boundary_pressure: int = 0
    evidence_event_ids: tuple[str, ...] = ()
    version: int = 0


class RelationshipStage(str, Enum):
    GUARDED = "警戒"
    DISTANT = "疏远"
    STRANGER = "陌生"
    KNOWS = "认识"
    FAMILIAR = "熟悉"
    CLOSE = "亲近"
    IN_SYNC = "默契"


def relationship_stage(value: float) -> RelationshipStage:
    score = max(-100.0, min(100.0, float(value)))
    if score <= -40.0:
        return RelationshipStage.GUARDED
    if score <= -10.0:
        return RelationshipStage.DISTANT
    if score < 10.0:
        return RelationshipStage.STRANGER
    if score < 30.0:
        return RelationshipStage.KNOWS
    if score < 55.0:
        return RelationshipStage.FAMILIAR
    if score < 80.0:
        return RelationshipStage.CLOSE
    return RelationshipStage.IN_SYNC


@dataclass(frozen=True)
class PublicAffection:
    value: float
    stage: RelationshipStage

    def __post_init__(self) -> None:
        value = round(max(-100.0, min(100.0, float(self.value))), 1)
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "stage", RelationshipStage(self.stage))

    @classmethod
    def from_projection(
        cls, state: RelationshipProjection
    ) -> "PublicAffection":
        raw = (
            state.familiarity * 0.15
            + state.warmth * 0.18
            + state.trust * 0.20
            + state.reciprocity * 0.12
            + state.play_acceptance * 0.08
            + state.reliability * 0.12
            + state.care_permission * 0.15
            - state.boundary_pressure * 0.40
        )
        value = round(max(-100.0, min(100.0, raw)), 1)
        return cls(value, relationship_stage(value))


_EVIDENCE_DIMENSIONS = {
    "interaction": "familiarity",
    "warm_exchange": "warmth",
    "trust_confirmed": "trust",
    "reciprocal_action": "reciprocity",
    "play_accepted": "play_acceptance",
    "reliable_help": "reliability",
    "care_permission": "care_permission",
    "boundary_pressure": "boundary_pressure",
}


class RelationshipProjector:
    def empty(self, persona_id: str, group_id: str, subject_id: str) -> RelationshipProjection:
        if not persona_id or not group_id or not subject_id:
            raise ValueError("relationship scope is required")
        return RelationshipProjection(persona_id, group_id, subject_id)

    def apply(
        self, state: RelationshipProjection, evidence: RelationshipEvidence
    ) -> RelationshipProjection:
        if evidence.event_id in state.evidence_event_ids:
            return state
        dimension = _EVIDENCE_DIMENSIONS.get(evidence.kind)
        if dimension is None:
            return state
        value = max(-100, min(100, getattr(state, dimension) + evidence.amount))
        return replace(
            state,
            **{dimension: value},
            evidence_event_ids=state.evidence_event_ids + (evidence.event_id,),
            version=state.version + 1,
        )

    @staticmethod
    def authorizes_capability(
        state: RelationshipProjection, capability: str
    ) -> bool:
        del state, capability
        return False

    @staticmethod
    def to_dict(state: RelationshipProjection) -> dict[str, object]:
        return asdict(state)

    @staticmethod
    def from_dict(payload: Mapping[str, object]) -> RelationshipProjection:
        values = dict(payload)
        values["evidence_event_ids"] = tuple(values.get("evidence_event_ids", ()))
        return RelationshipProjection(**values)


__all__ = (
    "PublicAffection",
    "RelationshipEvidence",
    "RelationshipProjection",
    "RelationshipProjector",
    "RelationshipStage",
    "relationship_stage",
)
