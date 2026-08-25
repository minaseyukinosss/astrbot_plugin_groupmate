from __future__ import annotations

from groupmate.social_runtime.expression import ExpressionPlanner
from groupmate.social_runtime.persona.profile import GroupmatePersonaProfile


def _profile():
    profile = GroupmatePersonaProfile.default().to_mapping()
    profile["identity"]["name"] = "爱弥斯"
    return profile


def test_direct_expression_reacts_then_answers_and_may_leave_a_hook():
    plan = ExpressionPlanner().plan(
        lane="DIRECT_FAST",
        act="respond_to_direct_interaction",
        source_text="小爱你怎么不理我",
        persona_profile=_profile(),
    )

    assert plan.reaction_stance == "acknowledge_relationship"
    assert plan.core_response_goal == "respond_to_direct_interaction"
    assert plan.persona_cues[0] == "爱弥斯"
    assert plan.followup_hook == "optional_if_natural"
    assert plan.message_count in {1, 2}


def test_continuation_expression_keeps_the_current_exchange():
    plan = ExpressionPlanner().plan(
        lane="CONTINUATION",
        act="continue_dialogue",
        source_text="然后呢",
        persona_profile=_profile(),
    )

    assert plan.reaction_stance == "continue_current_exchange"
    assert plan.followup_hook == "optional_if_natural"
    assert plan.message_count == 1


def test_ambient_expression_uses_a_low_interruption_posture():
    plan = ExpressionPlanner().plan(
        lane="AMBIENT",
        act="join_topic",
        source_text="大家在讨论项目",
        persona_profile=_profile(),
    )

    assert plan.reaction_stance == "attentive"
    assert plan.followup_hook == "only_if_it_adds_value"
