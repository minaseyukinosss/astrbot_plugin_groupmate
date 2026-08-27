import pytest

from groupmate.social_runtime.social_scenes import (
    ChorusTarget,
    ChorusTone,
    SocialScene,
    TargetScope,
)


def test_social_scene_requires_concrete_subject_and_bounded_confidence():
    with pytest.raises(ValueError, match="literal_subject"):
        SocialScene.create(
            scene_kind="technical_help",
            target_scope="INDIVIDUAL",
            target_id="u1",
            literal_subject="",
            user_move="asks_for_diagnosis",
            continuity_event_ids=("m1",),
            confidence=0.9,
        )

    with pytest.raises(ValueError, match="confidence"):
        SocialScene.create(
            scene_kind="technical_help",
            target_scope="INDIVIDUAL",
            target_id="u1",
            literal_subject="报错",
            user_move="asks_for_diagnosis",
            continuity_event_ids=("m1",),
            confidence=1.1,
        )


def test_individual_scene_requires_target_but_group_scene_does_not():
    with pytest.raises(ValueError, match="target_id"):
        SocialScene.create(
            scene_kind="direct_greeting",
            target_scope="INDIVIDUAL",
            target_id=None,
            literal_subject="问候",
            user_move="greets",
            continuity_event_ids=("m1",),
            confidence=0.9,
        )

    scene = SocialScene.create(
        scene_kind="group_pile_on",
        target_scope="GROUP",
        target_id=None,
        literal_subject="群体起哄",
        user_move="group_teases_self",
        continuity_event_ids=("m1", "m2"),
        confidence=0.8,
    )
    assert scene.target_scope is TargetScope.GROUP


def test_self_targeted_chorus_keeps_evidence_but_not_member_id():
    scene = SocialScene.create(
        scene_kind="group_chorus",
        target_scope="GROUP",
        target_id=None,
        literal_subject="爱弥斯",
        user_move="chorus_about_self",
        continuity_event_ids=("m1", "m2"),
        repetition_count=2,
        chorus_target="SELF",
        chorus_target_id=None,
        chorus_chain_id="chorus:abc",
        chorus_payload="爱弥斯又嘴硬",
        chorus_event_ids=("m1", "m2"),
        chorus_participant_ids=("u1", "u2"),
        chorus_tone="SAFE_BANTER",
        confidence=0.95,
    )
    assert scene.chorus_target is ChorusTarget.SELF
    assert scene.chorus_tone is ChorusTone.SAFE_BANTER
    assert scene.chorus_target_id is None


def test_member_chorus_requires_known_target_and_complete_evidence():
    with pytest.raises(ValueError, match="chorus_target_id"):
        SocialScene.create(
            scene_kind="group_chorus",
            target_scope="GROUP",
            target_id=None,
            literal_subject="群友",
            user_move="chorus_about_member",
            continuity_event_ids=("m1", "m2"),
            repetition_count=2,
            chorus_target="MEMBER",
            chorus_chain_id="chorus:abc",
            chorus_payload="小林今天请客",
            chorus_event_ids=("m1", "m2"),
            chorus_participant_ids=("u1", "u2"),
            chorus_tone="SAFE_BANTER",
            confidence=0.95,
        )
    with pytest.raises(ValueError, match="subset"):
        SocialScene.create(
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
            chorus_event_ids=("m1", "invented"),
            chorus_participant_ids=("u1", "u2"),
            chorus_tone="SAFE_BANTER",
            confidence=0.95,
        )
