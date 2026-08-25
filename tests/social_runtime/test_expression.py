from __future__ import annotations

from groupmate.social_runtime.expression import ExpressionPlanner
from groupmate.social_runtime.persona.profile import GroupmatePersonaProfile
from groupmate.social_runtime.persona.presets import AEMEATH_CURRENT_CANON
from groupmate.social_runtime.society.relationships import (
    PublicAffection,
    RelationshipStage,
)


def _profile():
    profile = GroupmatePersonaProfile.default().to_mapping()
    profile["identity"]["name"] = "爱弥斯"
    profile["canon"] = AEMEATH_CURRENT_CANON.to_mapping()
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


def test_unrelated_chat_uses_no_explicit_persona_material():
    plan = ExpressionPlanner().plan(
        lane="DIRECT_FAST",
        act="respond_to_direct_interaction",
        source_text="小爱，陪我聊会儿",
        persona_profile=_profile(),
        relationship=PublicAffection(0.0, RelationshipStage.STRANGER),
        recent_outputs=(),
    )

    assert plan.explicit_material is None
    assert plan.material_reason == "no_relevant_material"
    assert plan.relationship_stage == "陌生"


def test_relevant_game_topic_may_select_one_daily_life_fact():
    plan = ExpressionPlanner().plan(
        lane="CONTINUATION",
        act="continue_dialogue",
        source_text="你最近在玩什么游戏",
        persona_profile=_profile(),
        relationship=PublicAffection(35.0, RelationshipStage.FAMILIAR),
        recent_outputs=(),
    )

    assert plan.explicit_material is not None
    assert "游戏" in plan.explicit_material
    assert plan.material_reason == "topic_relevant"


def test_recent_material_is_cooled_down():
    plan = ExpressionPlanner().plan(
        lane="CONTINUATION",
        act="continue_dialogue",
        source_text="说说你的机兵",
        persona_profile=_profile(),
        relationship=PublicAffection(35.0, RelationshipStage.FAMILIAR),
        recent_outputs=("刚刚才说过隧者兵装。",),
    )

    assert plan.explicit_material is None
    assert plan.material_reason == "recently_repeated"


def test_guarded_relationship_changes_boundary_posture_not_action():
    plan = ExpressionPlanner().plan(
        lane="DIRECT_FAST",
        act="respond_to_direct_interaction",
        source_text="你理我一下",
        persona_profile=_profile(),
        relationship=PublicAffection(-45.0, RelationshipStage.GUARDED),
        recent_outputs=(),
    )

    assert plan.core_response_goal == "respond_to_direct_interaction"
    assert plan.relationship_stage == "警戒"
    assert "冷静" in plan.boundary_style


def test_relevant_relationship_memory_is_bounded_in_expression_plan():
    plan = ExpressionPlanner().plan(
        lane="DIRECT_FAST",
        act="respond_to_direct_interaction",
        source_text="现在陪我聊天",
        persona_profile=_profile(),
        relationship=PublicAffection(-45.0, RelationshipStage.GUARDED),
        relationship_memory_cues=(
            "未修复边界事件：成员上次明确辱骂爱弥斯",
            "不应进入的第三条",
            "也不应进入的第四条",
        ),
    )

    assert plan.relationship_memory_cues == (
        "未修复边界事件：成员上次明确辱骂爱弥斯",
        "不应进入的第三条",
    )


def test_close_relationship_has_warmer_distance_without_forcing_more_words():
    plan = ExpressionPlanner().plan(
        lane="DIRECT_FAST",
        act="respond_to_direct_interaction",
        source_text="在吗",
        persona_profile=_profile(),
        relationship=PublicAffection(60.0, RelationshipStage.CLOSE),
    )

    assert "亲近" in plan.boundary_style
    assert plan.message_count == 1
