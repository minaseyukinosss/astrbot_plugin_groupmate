"""Evidence-derived, group-scoped multidimensional relationships."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
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


__all__ = ("RelationshipEvidence", "RelationshipProjection", "RelationshipProjector")
