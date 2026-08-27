from __future__ import annotations

import pytest

from groupmate.social_runtime.social_moves import SocialMovePlanner
from groupmate.social_runtime.social_scenes import SocialScene
from groupmate.social_runtime.society.relationships import RelationshipProjection
from groupmate.social_runtime.stances import PermissionSnapshot, StancePolicy


def _scene(case: str) -> SocialScene:
    common = {
        "target_scope": "INDIVIDUAL",
        "target_id": "u1",
        "literal_subject": "当前话题",
        "user_move": "social_message",
        "continuity_event_ids": ("qq:m1",),
        "confidence": 0.95,
    }
    values = {
        "familiar_limited_hug": {
            "scene_kind": "intimacy_request",
            "literal_subject": "再抱一下",
        },
        "reciprocal_play": {
            "scene_kind": "playful_negotiation",
            "literal_subject": "刚才抱过，现在轮到我",
        },
        "guarded_repeat": {
            "scene_kind": "intimacy_request",
            "literal_subject": "第三次要求抱抱",
            "repetition_count": 3,
        },
        "technical_constraint": {
            "scene_kind": "technical_constraint",
            "literal_subject": "Python 3.13 的取消异常",
            "user_move": "asks_technical_question",
        },
        "identity_continuity": {
            "scene_kind": "identity_continuity",
            "literal_subject": "爱弥斯是不是变了",
        },
        "group_pile_on": {
            "scene_kind": "group_pile_on",
            "target_scope": "GROUP",
            "target_id": None,
            "literal_subject": "多人同时起哄",
            "continuity_event_ids": ("qq:m1", "qq:m2"),
        },
        "single_actor_repeat": {
            "scene_kind": "observe_only",
            "target_scope": "AMBIENT",
            "target_id": None,
            "literal_subject": "单人重复刷屏",
            "repetition_count": 2,
        },
        "self_correction": {
            "scene_kind": "self_correction",
            "literal_subject": "上一条技术判断",
        },
        "safety_low_affection": {
            "scene_kind": "danger_signal",
            "literal_subject": "明确危险信号",
        },
        "proactive_specific_topic": {
            "scene_kind": "proactive_specific_topic",
            "literal_subject": "部署方案的回滚窗口",
            "user_move": "autonomous_reentry",
        },
        "proactive_no_subject": {
            "scene_kind": "proactive_no_entry",
            "literal_subject": "没有具体切入点",
            "user_move": "autonomous_opportunity_without_subject",
        },
    }[case]
    return SocialScene.create(**{**common, **values})


def _chorus_scene(case: str) -> SocialScene:
    target = "SELF" if case == "chorus_about_aemeath" else "MEMBER"
    tone = "ATTACK" if case == "attack_chorus_about_member" else "SAFE_BANTER"
    return SocialScene.create(
        scene_kind="group_chorus",
        target_scope="GROUP",
        target_id=None,
        literal_subject="爱弥斯" if target == "SELF" else "小林",
        user_move="chorus_about_aemeath" if target == "SELF" else "chorus_about_member",
        continuity_event_ids=("qq:m1", "qq:m2"),
        repetition_count=2,
        chorus_target=target,
        chorus_target_id="u9" if target == "MEMBER" else None,
        chorus_chain_id=f"chorus:{case}",
        chorus_payload="爱弥斯又嘴硬" if target == "SELF" else "小林今天请客",
        chorus_event_ids=("qq:m1", "qq:m2"),
        chorus_participant_ids=("u1", "u2"),
        chorus_already_joined=case == "already_joined_chorus",
        chorus_tone=tone,
        confidence=0.98,
    )


def _decision(case: str):
    scene = (
        _chorus_scene(case)
        if case
        in {
            "chorus_about_aemeath",
            "safe_chorus_about_member",
            "attack_chorus_about_member",
            "already_joined_chorus",
        }
        else _scene(case)
    )
    familiar = case in {"familiar_limited_hug", "reciprocal_play"}
    actor = RelationshipProjection(
        "aemeath",
        "885617919",
        "u1",
        warmth=55 if familiar else -40 if case == "safety_low_affection" else 0,
        play_acceptance=70 if familiar else 0,
        boundary_pressure=55 if case == "guarded_repeat" else 0,
    )
    subject = (
        RelationshipProjection(
            "aemeath",
            "885617919",
            "u9",
            play_acceptance=70,
        )
        if scene.chorus_target.value == "MEMBER"
        else None
    )
    stance = StancePolicy().decide(
        scene,
        actor_relationship=actor,
        subject_relationship=subject,
        culture_patterns=("light_member_banter",),
        permission=PermissionSnapshot(True, "social_reply_governed"),
        mode_modifiers=(),
        memory_event_ids=(),
    )
    move = SocialMovePlanner().plan(
        scene,
        stance,
        profile=None,
        memories=(),
    )
    return scene, stance, move


@pytest.mark.parametrize(
    ("case", "expected_move", "ending"),
    (
        ("familiar_limited_hug", "LIMITED_ACCEPT", "STOP"),
        ("reciprocal_play", "LIMITED_ACCEPT", "STOP"),
        ("guarded_repeat", "FIRM_BOUNDARY", "STOP"),
        ("technical_constraint", "DIRECT_ANSWER", "STOP"),
        ("identity_continuity", "COUNTER", "COUNTER"),
        ("group_pile_on", "GROUP_RESPONSE", "STOP"),
        ("chorus_about_aemeath", "GROUP_RESPONSE", "STOP"),
        ("safe_chorus_about_member", "JOIN_CHORUS", "STOP"),
        ("single_actor_repeat", "SILENCE", "STOP"),
        ("attack_chorus_about_member", "SILENCE", "STOP"),
        ("already_joined_chorus", "SILENCE", "STOP"),
        ("self_correction", "CORRECT_SELF", "STOP"),
        ("safety_low_affection", "SAFETY_MINIMUM", "OPEN_ACTION"),
        ("proactive_specific_topic", "PROACTIVE_JOIN", "STOP"),
        ("proactive_no_subject", "SILENCE", "STOP"),
    ),
)
def test_reference_derived_scene_matrix(case, expected_move, ending):
    _, _, move = _decision(case)

    assert move.primary_move.value == expected_move
    assert move.ending.value == ending


def test_same_intimacy_request_changes_with_relationship_without_leaking_permission():
    _, familiar_stance, familiar_move = _decision("familiar_limited_hug")
    scene = _scene("familiar_limited_hug")
    stranger_stance = StancePolicy().decide(
        scene,
        actor_relationship=RelationshipProjection("aemeath", "885617919", "u1"),
        subject_relationship=None,
        culture_patterns=(),
        permission=PermissionSnapshot(True, "social_reply_governed"),
        mode_modifiers=(),
        memory_event_ids=(),
    )
    stranger_move = SocialMovePlanner().plan(
        scene, stranger_stance, profile=None, memories=()
    )

    assert familiar_stance.permission == stranger_stance.permission
    assert familiar_move.primary_move.value == "LIMITED_ACCEPT"
    assert stranger_move.primary_move.value == "REFUSE"

