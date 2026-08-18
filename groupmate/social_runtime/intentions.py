"""Deterministic persona goals and evidence-backed candidate intentions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .cognition.blackboard import BlackboardEntry, BlackboardSnapshot


@dataclass(frozen=True)
class PersonaGoal:
    goal_id: str
    description: str


STABLE_GOALS = (
    PersonaGoal("identity", "保持身份和价值观一致"),
    PersonaGoal("bounded_relationship", "建立双向且有边界的关系"),
    PersonaGoal("useful_help", "在有价值时提供帮助"),
    PersonaGoal("commitment", "完成已接受任务和承诺"),
    PersonaGoal("culture", "参与群文化但不垄断"),
    PersonaGoal("privacy", "保护边界和隐私"),
    PersonaGoal("energy", "保存精力并在不确定时观察"),
)


@dataclass(frozen=True)
class CandidateIntention:
    intention_id: str
    kind: str
    target_id: str | None
    topic_id: str | None
    evidence_event_ids: tuple[str, ...]
    proposed_act: str
    obligation: float
    relevance: float
    relational_value: float
    continuity_value: float
    novelty: float
    urgency: float
    persona_fit: float
    state_fit: float
    information_gain: float
    disruption_cost: float
    uncertainty_cost: float
    repetition_cost: float
    resource_cost: float
    risk: float
    expires_at: int

    def __post_init__(self) -> None:
        if not self.intention_id or not self.kind or not self.proposed_act:
            raise ValueError("intention identity and proposed_act are required")
        if self.kind != "OBSERVE" and not self.evidence_event_ids:
            raise ValueError("actionable intention requires evidence")
        if self.expires_at < 0:
            raise ValueError("expires_at must not be negative")


_OBSERVATION_MAP = {
    "help_request": ("HELP", "answer_help_request"),
    "care_signal": ("CARE", "offer_bounded_care"),
    "humor_signal": ("PLAY", "join_playfully"),
    "greeting": ("ACKNOWLEDGE", "acknowledge_greeting"),
    "boundary_signal": ("BOUNDARY", "maintain_boundary"),
    "task_request": ("ACCEPT_TASK", "consider_task_request"),
}


class IntentionEngine:
    def propose(
        self, blackboard: BlackboardSnapshot, now: int
    ) -> tuple[CandidateIntention, ...]:
        if blackboard.degraded:
            return (
                self._candidate(
                    kind="OBSERVE",
                    target_id=None,
                    topic_id=None,
                    evidence=(),
                    proposed_act="observe_without_action",
                    expires_at=int(now) + 10,
                    features={"uncertainty_cost": 1.0},
                ),
            )

        candidates = []
        for entry in blackboard.entries:
            mapping = _OBSERVATION_MAP.get(entry.observation.kind)
            if mapping is None:
                continue
            kind, proposed_act = mapping
            proposition = entry.observation.proposition
            confidence = entry.observation.confidence
            candidates.append(
                self._candidate(
                    kind=kind,
                    target_id=self._optional_text(proposition.get("subject_id")),
                    topic_id=self._optional_text(proposition.get("topic_id")),
                    evidence=entry.observation.evidence_event_ids,
                    proposed_act=proposed_act,
                    expires_at=entry.observation.expires_at,
                    features=self._features(kind, confidence, entry),
                )
            )
        return tuple(sorted(candidates, key=lambda item: item.intention_id))

    def _candidate(
        self,
        *,
        kind: str,
        target_id: str | None,
        topic_id: str | None,
        evidence: tuple[str, ...],
        proposed_act: str,
        expires_at: int,
        features: dict[str, float],
    ) -> CandidateIntention:
        identity = {
            "kind": kind,
            "target_id": target_id,
            "topic_id": topic_id,
            "evidence": evidence,
            "proposed_act": proposed_act,
            "expires_at": expires_at,
        }
        digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:24]
        values = {
            "obligation": 0.0,
            "relevance": 0.0,
            "relational_value": 0.0,
            "continuity_value": 0.0,
            "novelty": 0.0,
            "urgency": 0.0,
            "persona_fit": 1.0,
            "state_fit": 1.0,
            "information_gain": 0.0,
            "disruption_cost": 0.0,
            "uncertainty_cost": 0.0,
            "repetition_cost": 0.0,
            "resource_cost": 0.0,
            "risk": 0.0,
        }
        values.update(features)
        return CandidateIntention(
            intention_id=f"intention:{digest}",
            kind=kind,
            target_id=target_id,
            topic_id=topic_id,
            evidence_event_ids=evidence,
            proposed_act=proposed_act,
            expires_at=expires_at,
            **values,
        )

    @staticmethod
    def _features(
        kind: str, confidence: float, entry: BlackboardEntry
    ) -> dict[str, float]:
        return {
            "obligation": confidence if kind in {"HELP", "BOUNDARY", "ACCEPT_TASK"} else 0.0,
            "relevance": confidence,
            "relational_value": confidence if kind in {"CARE", "PLAY"} else 0.0,
            "urgency": confidence if kind in {"BOUNDARY", "HELP"} else 0.0,
            "information_gain": confidence if kind == "HELP" else 0.0,
            "uncertainty_cost": 0.8 if entry.conflict else 1.0 - confidence,
            "risk": 0.2 if kind in {"CARE", "PLAY"} else 0.0,
        }

    @staticmethod
    def _optional_text(value: object) -> str | None:
        text = str(value or "").strip()
        return text or None


__all__ = ("CandidateIntention", "IntentionEngine", "PersonaGoal", "STABLE_GOALS")
