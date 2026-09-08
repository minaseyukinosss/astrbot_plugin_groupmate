import pytest
from dataclasses import replace

from groupmate.social_runtime.social_moves import (
    DecisionFact,
    Ending,
    MediaIntent,
    RealizationMode,
    SocialMove,
    SocialMovePlan,
    SocialMovePlanner,
)
from groupmate.social_runtime.social_scenes import SocialScene
from groupmate.social_runtime.stances import PermissionSnapshot, StanceDecision


def test_decision_fact_keeps_source_evidence_and_has_stable_identity():
    fact = DecisionFact.create(
        category="required_input",
        text="  需要报错首段和版本号  ",
        source_event_ids=("m1", "m1"),
    )
    same = DecisionFact.create(
        category="required_input",
        text="需要报错首段和版本号",
        source_event_ids=("m1",),
    )

    assert fact.fact_id.startswith("decision-fact:")
    assert fact.source_event_ids == ("m1",)
    assert fact == same


def test_social_move_question_requires_a_real_information_gap():
    with pytest.raises(ValueError, match="question ending"):
        SocialMovePlan(
            primary_move=SocialMove.DIRECT_ANSWER,
            secondary_move=None,
            must_say=(),
            may_say=(),
            must_not_say=(),
            mention_event_ids=(),
            ask_for=(),
            ending=Ending.QUESTION,
            media_intent=MediaIntent.NONE,
        )


def test_join_chorus_requires_frozen_payload_and_exact_mode():
    with pytest.raises(ValueError, match="verbatim_payload"):
        SocialMovePlan(
            primary_move=SocialMove.JOIN_CHORUS,
            secondary_move=None,
            must_say=(),
            may_say=(),
            must_not_say=(),
            mention_event_ids=("m1", "m2"),
            ask_for=(),
            ending=Ending.STOP,
            media_intent=MediaIntent.NONE,
            realization_mode=RealizationMode.EXACT_CHORUS,
            verbatim_payload=None,
            chorus_chain_id="chorus:abc",
        )

    with pytest.raises(ValueError, match="EXACT_CHORUS"):
        SocialMovePlan(
            primary_move=SocialMove.JOIN_CHORUS,
            secondary_move=None,
            must_say=(),
            may_say=(),
            must_not_say=(),
            mention_event_ids=("m1", "m2"),
            ask_for=(),
            ending=Ending.STOP,
            media_intent=MediaIntent.NONE,
            realization_mode=RealizationMode.GENERATED,
            verbatim_payload="小林今天请客",
            chorus_chain_id="chorus:abc",
        )


def test_non_chorus_move_cannot_smuggle_verbatim_text():
    with pytest.raises(ValueError, match="only JOIN_CHORUS"):
        SocialMovePlan(
            primary_move=SocialMove.GROUP_RESPONSE,
            secondary_move=None,
            must_say=(),
            may_say=(),
            must_not_say=(),
            mention_event_ids=("m1", "m2"),
            ask_for=(),
            ending=Ending.STOP,
            media_intent=MediaIntent.NONE,
            verbatim_payload="爱弥斯又嘴硬",
        )


def test_move_plan_deduplicates_bounded_reference_tuples():
    fact = DecisionFact.create(
        category="boundary",
        text="这是第三次问了，不行。",
        source_event_ids=("m1", "m2"),
    )
    plan = SocialMovePlan.create(
        primary_move="FIRM_BOUNDARY",
        must_say=(fact, fact),
        mention_event_ids=("m1", "m1", "m2"),
        ending="STOP",
        media_intent="NONE",
    )

    assert plan.must_say == (fact,)
    assert plan.mention_event_ids == ("m1", "m2")


def test_move_knowledge_authority_is_bounded_and_separate_from_decision_facts():
    plan = SocialMovePlan.create(
        primary_move="DIRECT_ANSWER",
        knowledge_policy="grounded",
        may_use_knowledge_ids=("knowledge:stable", "knowledge:stable"),
        prohibited_assertion_classes=("numeric",),
    )

    assert plan.knowledge_policy.value == "grounded"
    assert plan.may_use_knowledge_ids == ("knowledge:stable",)
    assert tuple(item.value for item in plan.prohibited_assertion_classes) == (
        "numeric",
    )

    knowledge_as_decision = DecisionFact(
        fact_id="knowledge:stable",
        category="knowledge",
        text="不应伪装成决策事实",
        source_event_ids=("m1",),
    )
    with pytest.raises(ValueError, match="separate"):
        SocialMovePlan.create(
            primary_move="DIRECT_ANSWER",
            knowledge_policy="grounded",
            must_say=(knowledge_as_decision,),
            must_use_knowledge_ids=("knowledge:stable",),
        )
    with pytest.raises(ValueError, match="unsupported"):
        SocialMovePlan.create(
            primary_move="DIRECT_ANSWER",
            prohibited_assertion_classes=("invented-risk",),
        )


@pytest.mark.parametrize("move", ("SILENCE", "JOIN_CHORUS"))
def test_non_generated_moves_cannot_carry_knowledge_authority(move):
    values = {
        "primary_move": move,
        "knowledge_policy": "grounded",
        "may_use_knowledge_ids": ("knowledge:stable",),
    }
    if move == "JOIN_CHORUS":
        values.update(
            realization_mode="EXACT_CHORUS",
            verbatim_payload="复读内容",
            chorus_chain_id="chorus:1",
        )

    with pytest.raises(ValueError, match="knowledge none"):
        SocialMovePlan.create(**values)


def _scene(scene_kind, *, information_gaps=(), repetition_count=0):
    return SocialScene.create(
        scene_kind=scene_kind,
        target_scope="INDIVIDUAL",
        target_id="u1",
        literal_subject="当前请求",
        user_move="asks",
        continuity_event_ids=("m1",),
        information_gaps=information_gaps,
        repetition_count=repetition_count,
        confidence=0.9,
    )


def _stance(willingness, *, attitude="NEUTRAL", boundary="NONE"):
    return StanceDecision.create(
        attitude=attitude,
        willingness=willingness,
        boundary=boundary,
        concession="NONE",
        effort="NORMAL",
        initiative="ALLOW",
        reason_event_ids=("m1",),
        permission=PermissionSnapshot(True, "social_reply"),
    )


def test_response_act_drives_expression_without_overriding_hard_moves():
    planner = SocialMovePlanner()
    for act in ("answer", "acknowledge", "react", "follow_up", "close"):
        scene = replace(_scene("成员自然接话，非固定英文分类"), response_act=act)
        move = planner.plan(scene, _stance("WILLING"), profile=None, memories=())
        assert move.response_act == act
        assert move.primary_move is SocialMove.DIRECT_ANSWER
        assert move.ending is (Ending.QUESTION if act == "follow_up" else Ending.STOP)
        assert move.ask_for == ()  # Natural follow-up is not an evidence request.
        refused = planner.plan(scene, _stance("UNWILLING", boundary="FIRM"), profile=None, memories=())
        assert refused.primary_move is SocialMove.FIRM_BOUNDARY
        assert refused.response_act is None
    legacy = planner.plan(_scene("contextual_teasing"), _stance("WILLING"), profile=None, memories=())
    assert legacy.primary_move is SocialMove.TEASE_FROM_CONTEXT
    assert legacy.response_act is None


def _chorus_scene(
    *, target, tone="SAFE_BANTER", target_id=None, payload="复读", already_joined=False
):
    return SocialScene.create(
        scene_kind="group_chorus",
        target_scope="GROUP",
        target_id=None,
        literal_subject="爱弥斯" if target == "SELF" else "小林",
        user_move="chorus",
        continuity_event_ids=("m1", "m2"),
        repetition_count=2,
        chorus_target=target,
        chorus_target_id=target_id,
        chorus_chain_id="chorus:abc",
        chorus_payload=payload,
        chorus_event_ids=("m1", "m2"),
        chorus_participant_ids=("u1", "u2"),
        chorus_already_joined=already_joined,
        chorus_tone=tone,
        confidence=0.95,
    )


def test_repeated_boundary_scene_becomes_one_firm_refusal_fact():
    plan = SocialMovePlanner().plan(
        _scene("repeated_boundary_test", repetition_count=3),
        _stance("UNWILLING", boundary="FIRM"),
        profile=None,
        memories=(),
    )
    assert plan.primary_move is SocialMove.FIRM_BOUNDARY
    assert plan.ending is Ending.STOP
    assert [item.category for item in plan.must_say] == ["boundary"]
    assert "第三次" in plan.must_say[0].text


def test_technical_help_with_real_gap_asks_only_for_needed_evidence():
    plan = SocialMovePlanner().plan(
        _scene("technical_help", information_gaps=("报错首段", "版本号")),
        _stance("WILLING", attitude="FOCUSED"),
        profile=None,
        memories=(),
    )
    assert plan.primary_move is SocialMove.REQUEST_NEEDED_EVIDENCE
    assert plan.ask_for == ("报错首段", "版本号")
    assert plan.ending is Ending.QUESTION


def test_self_targeted_chorus_becomes_generated_group_response():
    plan = SocialMovePlanner().plan(
        _chorus_scene(target="SELF", payload="爱弥斯又嘴硬"),
        _stance("WILLING", attitude="AMUSED"),
        profile=None,
        memories=(),
    )
    assert plan.primary_move is SocialMove.GROUP_RESPONSE
    assert plan.realization_mode is RealizationMode.GENERATED
    assert plan.verbatim_payload is None


def test_safe_member_chorus_joins_with_exact_frozen_payload_once():
    plan = SocialMovePlanner().plan(
        _chorus_scene(
            target="MEMBER", target_id="u9", payload="小林今天请客"
        ),
        _stance("WILLING", attitude="AMUSED"),
        profile=None,
        memories=(),
    )
    assert plan.primary_move is SocialMove.JOIN_CHORUS
    assert plan.realization_mode is RealizationMode.EXACT_CHORUS
    assert plan.verbatim_payload == "小林今天请客"
    assert plan.chorus_chain_id == "chorus:abc"


@pytest.mark.parametrize("tone", ("ATTACK", "DANGEROUS", "UNKNOWN"))
def test_unsafe_or_already_joined_member_chorus_is_silent(tone):
    scene = _chorus_scene(
        target="MEMBER",
        target_id="u9",
        payload="不应发送",
        tone=tone,
        already_joined=(tone == "UNKNOWN"),
    )
    plan = SocialMovePlanner().plan(
        scene, _stance("UNWILLING"), profile=None, memories=()
    )
    assert plan.primary_move is SocialMove.SILENCE


def test_proactive_scene_without_concrete_subject_is_silent():
    scene = SocialScene.create(
        scene_kind="proactive_no_entry",
        target_scope="AMBIENT",
        target_id=None,
        literal_subject="没有具体入口",
        user_move="ambient_activity",
        continuity_event_ids=("m1",),
        confidence=0.7,
    )
    plan = SocialMovePlanner().plan(
        scene, _stance("WILLING"), profile=None, memories=()
    )
    assert plan.primary_move is SocialMove.SILENCE
