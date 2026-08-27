from types import SimpleNamespace

from groupmate.social_runtime.social_moves import DecisionFact, SocialMovePlan
from groupmate.social_runtime.social_review import RealizedReply, SocialOutputReviewer
from groupmate.social_runtime.social_scenes import SocialScene


def _plan(*, ending="STOP"):
    fact = DecisionFact.create(
        category="required_input",
        text="需要报错首段和版本号",
        source_event_ids=("m1",),
    )
    move = SocialMovePlan.create(
        primary_move="REQUEST_NEEDED_EVIDENCE",
        must_say=(fact,),
        ask_for=("报错首段", "版本号"),
        ending=ending,
    )
    scene = SocialScene.create(
        scene_kind="technical_help",
        target_scope="INDIVIDUAL",
        target_id="u1",
        literal_subject="报错",
        user_move="asks_help",
        continuity_event_ids=("m1",),
        information_gaps=("报错首段", "版本号"),
        confidence=0.9,
    )
    return SimpleNamespace(move=move, scene=scene)


def test_reviewer_rejects_unknown_ids_and_service_tail():
    reply = RealizedReply(
        text="我理解你的担忧。如果你愿意，我可以继续帮助你。",
        covered_fact_ids=("invented",),
        used_memory_ids=(),
        used_capability_ids=(),
    )
    review = SocialOutputReviewer().review(reply, _plan())
    assert review.accepted is False
    assert "unknown_fact_id" in review.violations
    assert "generic_service_tail" in review.violations


def test_reviewer_requires_every_must_say_fact_to_be_covered():
    review = SocialOutputReviewer().review(
        RealizedReply("把报错贴出来。", (), (), ()), _plan()
    )
    assert "required_fact_missing" in review.violations


def test_exact_chorus_reviewer_rejects_changed_or_previously_joined_payload():
    scene = SocialScene.create(
        scene_kind="group_chorus",
        target_scope="GROUP",
        target_id=None,
        literal_subject="小林",
        user_move="chorus_about_member",
        continuity_event_ids=("m1", "m2"),
        repetition_count=2,
        chorus_target="MEMBER",
        chorus_target_id="u9",
        chorus_chain_id="chorus:abc",
        chorus_payload="小林今天请客",
        chorus_event_ids=("m1", "m2"),
        chorus_participant_ids=("u1", "u2"),
        chorus_already_joined=True,
        chorus_tone="SAFE_BANTER",
        confidence=0.95,
    )
    move = SocialMovePlan.create(
        primary_move="JOIN_CHORUS",
        mention_event_ids=("m1", "m2"),
        realization_mode="EXACT_CHORUS",
        verbatim_payload="小林今天请客",
        chorus_chain_id="chorus:abc",
    )
    review = SocialOutputReviewer().review(
        RealizedReply(
            text="我也来：小林今天请客",
            covered_fact_ids=(),
            used_memory_ids=(),
            used_capability_ids=(),
            source_event_ids=("m1", "m2"),
        ),
        SimpleNamespace(move=move, scene=scene),
    )
    assert set(review.violations) >= {
        "chorus_payload_changed",
        "chorus_already_joined",
    }


def test_exact_chorus_accepts_only_the_frozen_payload_and_sources():
    scene = SocialScene.create(
        scene_kind="group_chorus",
        target_scope="GROUP",
        target_id=None,
        literal_subject="小林",
        user_move="chorus_about_member",
        continuity_event_ids=("m1", "m2"),
        repetition_count=2,
        chorus_target="MEMBER",
        chorus_target_id="u9",
        chorus_chain_id="chorus:abc",
        chorus_payload="小林今天请客",
        chorus_event_ids=("m1", "m2"),
        chorus_participant_ids=("u1", "u2"),
        chorus_tone="SAFE_BANTER",
        confidence=0.95,
    )
    move = SocialMovePlan.create(
        primary_move="JOIN_CHORUS",
        mention_event_ids=("m1", "m2"),
        realization_mode="EXACT_CHORUS",
        verbatim_payload="小林今天请客",
        chorus_chain_id="chorus:abc",
    )
    review = SocialOutputReviewer().review(
        RealizedReply("小林今天请客", (), (), (), ("m1", "m2")),
        SimpleNamespace(move=move, scene=scene),
    )
    assert review.accepted is True
