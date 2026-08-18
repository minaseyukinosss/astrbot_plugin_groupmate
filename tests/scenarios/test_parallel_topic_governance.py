from __future__ import annotations

from groupmate.social_runtime.governor import GovernorContext, SocialGovernor
from groupmate.social_runtime.intentions import CandidateIntention


def _candidate(intention_id, target_id, topic_id, relevance):
    return CandidateIntention(
        intention_id=intention_id,
        kind="HELP",
        target_id=target_id,
        topic_id=topic_id,
        evidence_event_ids=(f"qq:{topic_id}",),
        proposed_act="answer_help_request",
        obligation=1,
        relevance=relevance,
        relational_value=1,
        continuity_value=1,
        novelty=1,
        urgency=1,
        persona_fit=1,
        state_fit=1,
        information_gain=1,
        disruption_cost=0,
        uncertainty_cost=0,
        repetition_cost=0,
        resource_cost=0,
        risk=0,
        expires_at=130,
    )


def test_parallel_topics_with_different_targets_never_merge_into_one_action():
    result = SocialGovernor().decide(
        (
            _candidate("project-help", "u1", "m1", 5),
            _candidate("dinner-help", "u2", "m2", 4),
        ),
        GovernorContext(
            now=100,
            scene_version=3,
            allowed_target_ids=("u1", "u2"),
            allowed_topic_ids=("m1", "m2"),
            privacy_allowed=True,
            boundary_active=False,
            paused=False,
            platform_available=True,
            capability_allowed=True,
            force_observe=False,
            rate_limited_until=None,
            minimum_utility=1,
        ),
    )

    assert result.outcome == "ACT"
    assert result.selected_intention_ids == ("project-help",)
    assert result.rejected[0].intention_id == "dinner-help"
    assert "different_target" in result.rejected[0].reason_codes
