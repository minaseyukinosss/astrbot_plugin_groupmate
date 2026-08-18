"""Ephemeral evidence blackboard for one cognition cycle."""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..attention import AttentionFrame
from .contracts import CognitiveObservation


class ObservationRejected(ValueError):
    """Raised when an observation is stale or outside the frozen frame."""


@dataclass(frozen=True)
class BlackboardEntry:
    observation: CognitiveObservation
    conflict: bool


@dataclass(frozen=True)
class BlackboardSnapshot:
    frame_id: str
    scene_version: int
    cost_level: int
    entries: tuple[BlackboardEntry, ...]
    conflict_count: int
    degraded: bool
    recommended_outcome: str | None
    diagnostics: tuple[str, ...]


class CognitionBlackboard:
    def __init__(self, frame: AttentionFrame, now: int) -> None:
        self.frame = frame
        self.now = int(now)
        self._observations: list[CognitiveObservation] = []

    def add(self, observation: CognitiveObservation) -> None:
        if observation.scene_version != self.frame.scene_version:
            raise ObservationRejected("observation scene_version does not match frame")
        if observation.expires_at <= self.now:
            raise ObservationRejected("observation is expired")
        allowed_evidence = set(self.frame.focus_event_ids)
        if not set(observation.evidence_event_ids) <= allowed_evidence:
            raise ObservationRejected("observation evidence is outside the frame")
        self._observations.append(observation)

    def snapshot(
        self,
        *,
        cost_level: int,
        degraded: bool = False,
        diagnostics: tuple[str, ...] = (),
    ) -> BlackboardSnapshot:
        groups: dict[tuple[object, ...], set[str]] = {}
        for observation in self._observations:
            key = self._claim_key(observation)
            groups.setdefault(key, set()).add(self._canonical_proposition(observation))
        conflicting_keys = {key for key, claims in groups.items() if len(claims) > 1}
        entries = tuple(
            BlackboardEntry(
                observation=observation,
                conflict=self._claim_key(observation) in conflicting_keys,
            )
            for observation in self._observations
        )
        return BlackboardSnapshot(
            frame_id=self.frame.frame_id,
            scene_version=self.frame.scene_version,
            cost_level=int(cost_level),
            entries=entries,
            conflict_count=len(conflicting_keys),
            degraded=bool(degraded),
            recommended_outcome="OBSERVE" if degraded else None,
            diagnostics=tuple(diagnostics),
        )

    @staticmethod
    def _claim_key(observation: CognitiveObservation) -> tuple[object, ...]:
        proposition = observation.proposition
        return (
            observation.kind,
            proposition.get("subject_id"),
            proposition.get("topic_id"),
            proposition.get("attribute"),
        )

    @staticmethod
    def _canonical_proposition(observation: CognitiveObservation) -> str:
        return json.dumps(
            dict(observation.proposition),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


__all__ = (
    "BlackboardEntry",
    "BlackboardSnapshot",
    "CognitionBlackboard",
    "ObservationRejected",
)
