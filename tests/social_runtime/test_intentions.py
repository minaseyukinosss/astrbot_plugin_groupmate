from __future__ import annotations

from groupmate.social_runtime.cognition.blackboard import (
    BlackboardEntry,
    BlackboardSnapshot,
)
from groupmate.social_runtime.cognition.contracts import CognitiveObservation
from groupmate.social_runtime.intentions import IntentionEngine


def _entry(kind, proposition, evidence=("qq:m1",)):
    return BlackboardEntry(
        observation=CognitiveObservation.create(
            worker="test.worker",
            kind=kind,
            proposition=proposition,
            confidence=0.9,
            evidence_event_ids=evidence,
            scene_version=3,
            expires_at=130,
            uncertainty=(),
        ),
        conflict=False,
    )


def test_intention_engine_maps_evidence_to_deterministic_scoped_candidates():
    blackboard = BlackboardSnapshot(
        frame_id="attention:1",
        scene_version=3,
        cost_level=1,
        entries=(
            _entry(
                "help_request",
                {"subject_id": "u1", "topic_id": "m1", "request": "解释报错"},
            ),
        ),
        conflict_count=0,
        degraded=False,
        recommended_outcome=None,
        diagnostics=(),
    )
    engine = IntentionEngine()

    first = engine.propose(blackboard, now=100)
    second = engine.propose(blackboard, now=100)

    assert first == second
    assert len(first) == 1
    candidate = first[0]
    assert candidate.kind == "HELP"
    assert candidate.target_id == "u1"
    assert candidate.topic_id == "m1"
    assert candidate.evidence_event_ids == ("qq:m1",)
    assert candidate.proposed_act == "answer_help_request"
    assert candidate.expires_at == 130


def test_degraded_blackboard_only_proposes_observe_intention():
    blackboard = BlackboardSnapshot(
        frame_id="attention:1",
        scene_version=3,
        cost_level=2,
        entries=(),
        conflict_count=0,
        degraded=True,
        recommended_outcome="OBSERVE",
        diagnostics=("cognition_budget_exhausted",),
    )

    candidates = IntentionEngine().propose(blackboard, now=100)

    assert [candidate.kind for candidate in candidates] == ["OBSERVE"]
    assert candidates[0].proposed_act == "observe_without_action"
