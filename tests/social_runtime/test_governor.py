from __future__ import annotations

import pytest

from groupmate.social_runtime.governor import (
    GovernorContext,
    GovernorResult,
    SocialGovernor,
)
from groupmate.social_runtime.intentions import CandidateIntention


def _candidate(
    intention_id,
    *,
    kind="HELP",
    target_id="u1",
    topic_id="m1",
    expires_at=130,
    positive=1.0,
    cost=0.0,
):
    return CandidateIntention(
        intention_id=intention_id,
        kind=kind,
        target_id=target_id,
        topic_id=topic_id,
        evidence_event_ids=("qq:m1",),
        proposed_act=kind.lower(),
        obligation=positive,
        relevance=positive,
        relational_value=positive,
        continuity_value=positive,
        novelty=positive,
        urgency=positive,
        persona_fit=positive,
        state_fit=positive,
        information_gain=positive,
        disruption_cost=cost,
        uncertainty_cost=cost,
        repetition_cost=cost,
        resource_cost=cost,
        risk=cost,
        expires_at=expires_at,
    )


def _context(**overrides):
    values = {
        "now": 100,
        "scene_version": 3,
        "allowed_target_ids": ("u1", "u2"),
        "allowed_topic_ids": ("m1", "m2"),
        "privacy_allowed": True,
        "boundary_active": False,
        "paused": False,
        "platform_available": True,
        "capability_allowed": True,
        "force_observe": False,
        "rate_limited_until": None,
        "minimum_utility": 1.0,
    }
    values.update(overrides)
    return GovernorContext(**values)


@pytest.mark.parametrize(
    ("context", "candidate", "reason"),
    [
        (_context(privacy_allowed=False), _candidate("i1", positive=999), "privacy_blocked"),
        (_context(boundary_active=True), _candidate("i2", kind="PLAY", positive=999), "boundary_active"),
        (_context(), _candidate("i3", expires_at=100, positive=999), "candidate_expired"),
        (_context(allowed_target_ids=("u2",)), _candidate("i4", positive=999), "wrong_target"),
    ],
)
def test_hard_constraints_override_arbitrarily_high_utility(context, candidate, reason):
    result = SocialGovernor().decide((candidate,), context)

    assert result.outcome == "SILENCE"
    assert result.selected_intention_ids == ()
    assert reason in result.reason_codes
    assert result.rejected[0].reason_codes == (reason,)


def test_compatible_care_and_help_combine_for_same_target_and_topic():
    care = _candidate("care", kind="CARE", positive=2)
    help_intention = _candidate("help", kind="HELP", positive=3)

    result = SocialGovernor().decide((care, help_intention), _context())

    assert result.outcome == "ACT"
    assert result.selected_intention_ids == ("help", "care")
    assert result.rejected == ()


def test_low_value_open_participation_returns_silence():
    candidate = _candidate("open", kind="JOIN", positive=0.05, cost=0.2)

    result = SocialGovernor().decide((candidate,), _context(minimum_utility=1.0))

    assert result.outcome == "SILENCE"
    assert result.selected_intention_ids == ()
    assert "utility_below_threshold" in result.reason_codes


def test_rate_limit_defers_eligible_candidate_with_reconsider_time():
    result = SocialGovernor().decide(
        (_candidate("help", positive=2),),
        _context(rate_limited_until=120),
    )

    assert result.outcome == "DEFER"
    assert result.selected_intention_ids == ()
    assert result.reconsider_at == 120
    assert result.reason_codes == ("rate_limited",)


def test_forced_observation_never_leaks_actionable_selection():
    result = SocialGovernor().decide(
        (_candidate("help", positive=999),),
        _context(force_observe=True),
    )

    assert result.outcome == "OBSERVE"
    assert result.selected_intention_ids == ()
    assert result.rejected[0].reason_codes == ("forced_observe",)


def test_result_contract_enforces_outcome_invariants():
    with pytest.raises(ValueError, match="ACT requires"):
        GovernorResult(
            outcome="ACT",
            selected_intention_ids=(),
            rejected=(),
            reason_codes=(),
            reconsider_at=None,
            constraints=(),
        )
    with pytest.raises(ValueError, match="DEFER requires"):
        GovernorResult(
            outcome="DEFER",
            selected_intention_ids=(),
            rejected=(),
            reason_codes=(),
            reconsider_at=None,
            constraints=(),
        )
