"""Validated relationship event proposals and strategy-owned scoring."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .relationships import (
    PublicAffection,
    RelationshipEvidence,
    RelationshipProjection,
    RelationshipProjector,
)


RELATIONSHIP_EVENT_KINDS = frozenset(
    {
        "interaction",
        "warm_exchange",
        "trust_confirmed",
        "reciprocal_action",
        "play_accepted",
        "reliable_help",
        "care_permission",
        "boundary_pressure",
        "repair_attempt",
        "repair_confirmed",
    }
)
RELATIONSHIP_SEVERITIES = ("minor", "ordinary", "significant", "severe")


@dataclass(frozen=True)
class RelationshipEventProposal:
    event_id: str
    persona_id: str
    group_id: str
    subject_id: str
    kind: str
    confidence: float
    severity: str
    summary: str
    source_event_ids: tuple[str, ...]
    occurred_at: int
    repair_of: str | None = None
    sensitivity: str = "normal"

    def __post_init__(self) -> None:
        for name in (
            "event_id",
            "persona_id",
            "group_id",
            "subject_id",
            "kind",
            "severity",
            "summary",
        ):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError("relationship event fields must not be empty")
            object.__setattr__(self, name, value)
        if self.kind not in RELATIONSHIP_EVENT_KINDS:
            raise ValueError("unknown relationship event kind")
        if self.severity not in RELATIONSHIP_SEVERITIES:
            raise ValueError("unknown relationship event severity")
        confidence = float(self.confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("relationship confidence must be between 0 and 1")
        object.__setattr__(self, "confidence", confidence)
        evidence = tuple(
            dict.fromkeys(
                str(item or "").strip()
                for item in self.source_event_ids
                if str(item or "").strip()
            )
        )
        if not 1 <= len(evidence) <= 8:
            raise ValueError("relationship event requires 1-8 evidence events")
        object.__setattr__(self, "source_event_ids", evidence)
        occurred_at = int(self.occurred_at)
        if occurred_at < 0:
            raise ValueError("relationship event time must not be negative")
        object.__setattr__(self, "occurred_at", occurred_at)
        if len(self.summary) > 240:
            raise ValueError("relationship event summary is too long")
        sensitivity = str(self.sensitivity or "normal").strip()
        if sensitivity not in {"normal", "sensitive", "restricted"}:
            raise ValueError("unknown relationship event sensitivity")
        object.__setattr__(self, "sensitivity", sensitivity)
        repair_of = str(self.repair_of or "").strip() or None
        if self.kind == "repair_confirmed" and repair_of is None:
            raise ValueError("confirmed repair requires repair_of")
        object.__setattr__(self, "repair_of", repair_of)


@dataclass(frozen=True)
class RelationshipEventDecision:
    outcome: str
    reason_codes: tuple[str, ...]
    proposal: RelationshipEventProposal
    evidence: RelationshipEvidence | None
    public_delta: float

    def __post_init__(self) -> None:
        if self.outcome not in {"ACCEPT", "REJECT", "SUGGEST", "DUPLICATE"}:
            raise ValueError("unknown relationship decision outcome")
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        object.__setattr__(self, "public_delta", round(float(self.public_delta), 1))


class RelationshipEventPolicy:
    _CONFIDENCE = {
        "interaction": 1.0,
        "reciprocal_action": 1.0,
        "boundary_pressure": 0.90,
        "repair_attempt": 0.88,
        "repair_confirmed": 0.88,
    }
    _AMOUNTS = {
        "interaction": (1, 1, 1, 1),
        "warm_exchange": (1, 2, 4, 6),
        "trust_confirmed": (1, 2, 4, 6),
        "reciprocal_action": (1, 1, 1, 1),
        "play_accepted": (1, 1, 2, 2),
        "reliable_help": (2, 2, 4, 6),
        "care_permission": (1, 1, 2, 2),
        "boundary_pressure": (1, 3, 8, 15),
        "repair_confirmed": (-1, -2, -4, -6),
    }
    _PROJECTION_KIND = {
        "repair_confirmed": "boundary_pressure",
    }

    def __init__(self, projector: RelationshipProjector | None = None) -> None:
        self._projector = projector or RelationshipProjector()

    def decide(
        self,
        proposal: RelationshipEventProposal,
        *,
        current: RelationshipProjection,
        positive_delta_today: float,
    ) -> RelationshipEventDecision:
        if (
            proposal.persona_id,
            proposal.group_id,
            proposal.subject_id,
        ) != (current.persona_id, current.group_id, current.subject_id):
            return self._reject(proposal, "scope_mismatch")
        threshold = self._CONFIDENCE.get(proposal.kind, 0.82)
        if proposal.confidence < threshold:
            return self._reject(proposal, "confidence_below_threshold")
        if proposal.kind == "repair_attempt":
            return RelationshipEventDecision(
                "ACCEPT", ("record_only",), proposal, None, 0.0
            )
        severity_index = RELATIONSHIP_SEVERITIES.index(proposal.severity)
        amount = self._AMOUNTS[proposal.kind][severity_index]
        evidence = RelationshipEvidence(
            proposal.event_id,
            self._PROJECTION_KIND.get(proposal.kind, proposal.kind),
            amount,
            proposal.occurred_at,
        )
        updated = self._projector.apply(current, evidence)
        public_delta = round(
            PublicAffection.from_projection(updated).value
            - PublicAffection.from_projection(current).value,
            1,
        )
        if (
            public_delta > 0
            and proposal.kind != "repair_confirmed"
            and float(positive_delta_today) + public_delta > 0.8 + 1e-9
        ):
            return self._reject(proposal, "positive_daily_budget_exhausted")
        return RelationshipEventDecision(
            "ACCEPT",
            ("policy_accepted",),
            proposal,
            evidence,
            public_delta,
        )

    @staticmethod
    def _reject(
        proposal: RelationshipEventProposal, reason: str
    ) -> RelationshipEventDecision:
        return RelationshipEventDecision(
            "REJECT", (reason,), proposal, None, 0.0
        )


class RelationshipEventService:
    def __init__(
        self,
        repository: object,
        policy: RelationshipEventPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._policy = policy or RelationshipEventPolicy()

    def process(
        self, proposal: RelationshipEventProposal, *, mode: str
    ) -> RelationshipEventDecision:
        return self._repository.process_relationship_event(
            proposal,
            mode=mode,
            policy=self._policy,
        )

    def snapshot(
        self, persona_id: str, group_id: str, subject_id: str
    ) -> RelationshipProjection:
        return self._repository.load_relationship(
            persona_id, group_id, subject_id
        )

    def decisions(
        self,
        persona_id: str,
        group_id: str,
        subject_id: str,
        *,
        since: int | None = None,
    ) -> tuple[RelationshipEventDecision, ...]:
        return self._repository.relationship_decisions(
            persona_id,
            group_id,
            subject_id,
            since=since,
        )


__all__ = (
    "RELATIONSHIP_EVENT_KINDS",
    "RELATIONSHIP_SEVERITIES",
    "RelationshipEventDecision",
    "RelationshipEventPolicy",
    "RelationshipEventProposal",
    "RelationshipEventService",
)
