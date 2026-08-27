import pytest

from groupmate.social_runtime.social_moves import (
    DecisionFact,
    Ending,
    MediaIntent,
    RealizationMode,
    SocialMove,
    SocialMovePlan,
)


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
