"""Strategy-first participation policy with model-gated ambient behavior."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .attention import AttentionFrame
from .cognition.blackboard import BlackboardSnapshot
from .chorus import ChorusEvidence
from .intentions import (
    CandidateIntention,
    IntentionEngine,
    create_candidate_intention,
)


class ParticipationLane(str, Enum):
    DIRECT_FAST = "DIRECT_FAST"
    CONTINUATION = "CONTINUATION"
    AMBIENT = "AMBIENT"


@dataclass(frozen=True)
class ParticipationProposal:
    lane: ParticipationLane
    candidates: tuple[CandidateIntention, ...]
    allow_degraded: bool
    diagnostics: tuple[str, ...]


class ParticipationPolicy:
    """Choose a deterministic lane before consulting model observations."""

    def __init__(self, intentions: IntentionEngine | None = None) -> None:
        self._intentions = intentions or IntentionEngine()

    def propose(
        self,
        frame: AttentionFrame,
        blackboard: BlackboardSnapshot,
        now: int,
        *,
        chorus_evidence: ChorusEvidence | None = None,
    ) -> ParticipationProposal:
        if frame.trigger_kind == "FAST" and not frame.requested_workers:
            return self._deterministic(
                frame,
                lane=ParticipationLane.DIRECT_FAST,
                kind="ACKNOWLEDGE",
                proposed_act="respond_to_direct_interaction",
                now=now,
                features={
                    "obligation": 1.0,
                    "relevance": 1.0,
                    "relational_value": 0.5,
                    "urgency": 1.0,
                },
            )
        if frame.trigger_kind == "CONTINUATION":
            return self._deterministic(
                frame,
                lane=ParticipationLane.CONTINUATION,
                kind="CONTINUE",
                proposed_act="continue_dialogue",
                now=now,
                features={
                    "relevance": 1.0,
                    "relational_value": 0.5,
                    "continuity_value": 1.0,
                },
            )
        if frame.trigger_kind == "TEMPORAL":
            return self._deterministic(
                frame,
                lane=ParticipationLane.AMBIENT,
                kind="PROACTIVE_CHECK",
                proposed_act="interpret_proactive_opportunity",
                now=now,
                features={
                    "relevance": 1.0,
                    "continuity_value": 1.0,
                    "relational_value": 0.3,
                    "disruption_cost": 0.1,
                    "uncertainty_cost": 0.1,
                },
            )
        if chorus_evidence is not None:
            return self._chorus_check(frame, chorus_evidence, now=int(now))
        return ParticipationProposal(
            lane=ParticipationLane.AMBIENT,
            candidates=self._intentions.propose(blackboard, int(now)),
            allow_degraded=False,
            diagnostics=("model_gated_ambient",),
        )

    @staticmethod
    def _deterministic(
        frame: AttentionFrame,
        *,
        lane: ParticipationLane,
        kind: str,
        proposed_act: str,
        now: int,
        features: dict[str, float],
    ) -> ParticipationProposal:
        target_id = next(iter(frame.candidate_audiences), None)
        topic_id = next(iter(frame.focus_topic_ids), None)
        evidence = frame.focus_event_ids
        if not target_id or not topic_id or not evidence:
            return ParticipationProposal(
                lane=lane,
                candidates=(),
                allow_degraded=False,
                diagnostics=("deterministic_scope_missing",),
            )
        candidate = create_candidate_intention(
            kind=kind,
            target_id=target_id,
            topic_id=topic_id,
            evidence=evidence,
            proposed_act=proposed_act,
            expires_at=int(now) + 30,
            features=features,
            identity_salt=lane.value,
        )
        return ParticipationProposal(
            lane=lane,
            candidates=(candidate,),
            allow_degraded=True,
            diagnostics=(f"deterministic_{lane.value.lower()}",),
        )

    @staticmethod
    def _chorus_check(
        frame: AttentionFrame,
        evidence: ChorusEvidence,
        *,
        now: int,
    ) -> ParticipationProposal:
        target_id = next(iter(frame.candidate_audiences), None)
        topic_id = next(iter(frame.focus_topic_ids), None)
        if not target_id or not topic_id:
            return ParticipationProposal(
                lane=ParticipationLane.AMBIENT,
                candidates=(),
                allow_degraded=False,
                diagnostics=("chorus_scope_missing",),
            )
        # 零重复成本只让确定的复读链进入语义判断；最终是否说话仍由 Governor 和场景链决定。
        candidate = create_candidate_intention(
            kind="CHORUS_CHECK",
            target_id=target_id,
            topic_id=topic_id,
            evidence=evidence.event_ids,
            proposed_act="interpret_group_chorus",
            expires_at=now + 30,
            features={
                "relevance": 1.0,
                "relational_value": 0.5,
                "continuity_value": 0.5,
                "novelty": 0.2,
                "disruption_cost": 0.1,
                "uncertainty_cost": 0.1,
                "repetition_cost": 0.0,
            },
            identity_salt=evidence.chain_id,
        )
        return ParticipationProposal(
            lane=ParticipationLane.AMBIENT,
            candidates=(candidate,),
            allow_degraded=True,
            diagnostics=("confirmed_chorus_semantic_check",),
        )


__all__ = (
    "ParticipationLane",
    "ParticipationPolicy",
    "ParticipationProposal",
)
