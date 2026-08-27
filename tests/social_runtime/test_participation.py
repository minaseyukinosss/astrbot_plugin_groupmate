from __future__ import annotations

from dataclasses import replace

from groupmate.social_runtime.attention import AttentionFrame
from groupmate.social_runtime.cognition.blackboard import BlackboardSnapshot
from groupmate.social_runtime.governor import SocialGovernor
from groupmate.social_runtime.chorus import ChorusEvidence
from groupmate.social_runtime.participation import (
    ParticipationLane,
    ParticipationPolicy,
)


def _frame(trigger_kind: str, *, target: str | None = "u1") -> AttentionFrame:
    return AttentionFrame(
        frame_id=f"attention:{trigger_kind.lower()}",
        group_id="885617919",
        scene_version=3,
        trigger_kind=trigger_kind,
        focus_topic_ids=("topic:m1",),
        focus_event_ids=("qq:m1",),
        candidate_audiences=(target,) if target else (),
        urgency="high" if trigger_kind != "AMBIENT" else "normal",
        deadline=100,
        requested_workers=() if trigger_kind != "AMBIENT" else (
            "scene_interpreter",
            "participation_assessor",
        ),
        persona_state_version=1,
        config_version=1,
    )


def _blackboard(*, degraded: bool) -> BlackboardSnapshot:
    return BlackboardSnapshot(
        frame_id="attention:test",
        scene_version=3,
        cost_level=1,
        entries=(),
        conflict_count=0,
        degraded=degraded,
        recommended_outcome="OBSERVE" if degraded else None,
        diagnostics=("worker_failed",) if degraded else (),
    )


def test_direct_fast_proposes_deterministic_actionable_candidate_when_degraded():
    policy = ParticipationPolicy()
    frame = _frame("FAST")

    first = policy.propose(frame, _blackboard(degraded=True), now=100)
    second = policy.propose(frame, _blackboard(degraded=True), now=100)

    assert first == second
    assert first.lane is ParticipationLane.DIRECT_FAST
    assert first.allow_degraded is True
    assert [item.kind for item in first.candidates] == ["ACKNOWLEDGE"]
    assert first.candidates[0].proposed_act == "respond_to_direct_interaction"
    assert SocialGovernor.utility(first.candidates[0]) >= 1.0


def test_continuation_proposes_scoped_candidate_when_degraded():
    proposal = ParticipationPolicy().propose(
        _frame("CONTINUATION"),
        _blackboard(degraded=True),
        now=100,
    )

    assert proposal.lane is ParticipationLane.CONTINUATION
    assert proposal.allow_degraded is True
    assert [item.kind for item in proposal.candidates] == ["CONTINUE"]
    assert proposal.candidates[0].target_id == "u1"
    assert proposal.candidates[0].topic_id == "topic:m1"
    assert proposal.candidates[0].evidence_event_ids == ("qq:m1",)


def test_ambient_degradation_remains_model_gated_and_observe_only():
    proposal = ParticipationPolicy().propose(
        _frame("AMBIENT"),
        _blackboard(degraded=True),
        now=100,
    )

    assert proposal.lane is ParticipationLane.AMBIENT
    assert proposal.allow_degraded is False
    assert [item.kind for item in proposal.candidates] == ["OBSERVE"]


def test_deterministic_lane_fails_closed_without_a_target():
    proposal = ParticipationPolicy().propose(
        replace(_frame("FAST"), candidate_audiences=()),
        _blackboard(degraded=True),
        now=100,
    )

    assert proposal.allow_degraded is False
    assert proposal.candidates == ()
    assert proposal.diagnostics == ("deterministic_scope_missing",)


def test_confirmed_chorus_gets_semantic_check_without_repeat_penalty():
    evidence = ChorusEvidence(
        chain_id="chorus:abc",
        payload="小林今天请客",
        normalized_key="小林今天请客",
        event_ids=("qq:m0", "qq:m1"),
        participant_ids=("u0", "u1"),
        already_joined=False,
    )

    proposal = ParticipationPolicy().propose(
        _frame("AMBIENT"),
        _blackboard(degraded=True),
        now=100,
        chorus_evidence=evidence,
    )

    assert proposal.lane is ParticipationLane.AMBIENT
    assert proposal.allow_degraded is True
    assert [item.kind for item in proposal.candidates] == ["CHORUS_CHECK"]
    assert proposal.candidates[0].evidence_event_ids == ("qq:m0", "qq:m1")
    assert proposal.candidates[0].repetition_cost == 0.0
    assert SocialGovernor.utility(proposal.candidates[0]) >= 1.0
