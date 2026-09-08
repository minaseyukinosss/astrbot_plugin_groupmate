import pytest

from groupmate.social_runtime.stances import (
    Attitude,
    Boundary,
    Concession,
    Effort,
    Initiative,
    PermissionSnapshot,
    StanceDecision,
    StancePolicy,
    Willingness,
)
from groupmate.social_runtime.social_scenes import SocialScene
from groupmate.social_runtime.society.relationships import RelationshipProjection


def test_stance_decision_freezes_permission_and_deduplicates_reasons():
    decision = StanceDecision.create(
        attitude="FOCUSED",
        willingness="WILLING",
        boundary="NONE",
        concession="NONE",
        effort="NORMAL",
        initiative="ALLOW",
        reason_event_ids=("m1", "m1", "m2"),
        permission=PermissionSnapshot(allowed=True, reason_code="social_reply"),
    )

    assert decision.attitude is Attitude.FOCUSED
    assert decision.willingness is Willingness.WILLING
    assert decision.boundary is Boundary.NONE
    assert decision.concession is Concession.NONE
    assert decision.effort is Effort.NORMAL
    assert decision.initiative is Initiative.ALLOW
    assert decision.reason_event_ids == ("m1", "m2")


def test_permission_snapshot_requires_a_stable_reason_code():
    with pytest.raises(ValueError, match="reason_code"):
        PermissionSnapshot(allowed=False, reason_code="")


def test_stance_rejects_non_permission_input():
    with pytest.raises(ValueError, match="permission"):
        StanceDecision(
            attitude="NEUTRAL",
            willingness="UNWILLING",
            boundary="FINAL",
            concession="NONE",
            effort="MINIMAL",
            initiative="AVOID",
            reason_event_ids=("m1",),
            permission={"allowed": False, "reason_code": "blocked"},
        )


def _scene(scene_kind, *, repetition_count=0):
    return SocialScene.create(
        scene_kind=scene_kind,
        target_scope="INDIVIDUAL",
        target_id="u1",
        literal_subject="当前请求",
        user_move="asks",
        continuity_event_ids=("m1",),
        repetition_count=repetition_count,
        confidence=0.9,
    )


def _relationship(subject="u1", **values):
    return RelationshipProjection(
        persona_id="aemeath", group_id="g1", subject_id=subject, **values
    )


def _member_chorus(*, tone="SAFE_BANTER"):
    return SocialScene.create(
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
        chorus_tone=tone,
        confidence=0.95,
    )


@pytest.mark.parametrize(
    ("relationship", "expected_willingness", "expected_boundary"),
    (
        (_relationship(warmth=55, play_acceptance=60), "LIMITED", "SOFT"),
        (_relationship(), "UNWILLING", "SOFT"),
        (_relationship(boundary_pressure=70), "UNWILLING", "FIRM"),
    ),
)
def test_same_intimacy_request_uses_relationship_for_willingness(
    relationship, expected_willingness, expected_boundary
):
    decision = StancePolicy().decide(
        _scene("intimacy_request"),
        actor_relationship=relationship,
        subject_relationship=None,
        culture_patterns=(),
        permission=PermissionSnapshot(True, "social_reply"),
        mode_modifiers=(),
        memory_event_ids=relationship.evidence_event_ids,
    )

    assert decision.willingness.value == expected_willingness
    assert decision.boundary.value == expected_boundary


def test_safety_minimum_ignores_low_affection_but_not_permission():
    decision = StancePolicy().decide(
        _scene("safety_signal"),
        actor_relationship=_relationship(boundary_pressure=100),
        subject_relationship=None,
        culture_patterns=(),
        permission=PermissionSnapshot(True, "safety_required"),
        mode_modifiers=(),
        memory_event_ids=("boundary-1",),
    )
    assert decision.willingness is Willingness.REQUIRED_MINIMUM

    blocked = StancePolicy().decide(
        _scene("safety_signal"),
        actor_relationship=_relationship(),
        subject_relationship=None,
        culture_patterns=(),
        permission=PermissionSnapshot(False, "platform_blocked"),
        mode_modifiers=(),
        memory_event_ids=(),
    )
    assert blocked.willingness is Willingness.UNWILLING
    assert blocked.boundary is Boundary.FINAL


@pytest.mark.parametrize(
    ("tone", "subject_pressure", "expected"),
    (
        ("SAFE_BANTER", 0, Willingness.WILLING),
        ("SAFE_BANTER", 70, Willingness.UNWILLING),
        ("ATTACK", 0, Willingness.UNWILLING),
        ("UNKNOWN", 0, Willingness.UNWILLING),
    ),
)
def test_member_chorus_uses_target_relationship_and_tone(
    tone, subject_pressure, expected
):
    decision = StancePolicy().decide(
        _member_chorus(tone=tone),
        actor_relationship=_relationship("u2", play_acceptance=90),
        subject_relationship=_relationship(
            "u9", play_acceptance=60, boundary_pressure=subject_pressure
        ),
        culture_patterns=("light_member_banter",),
        permission=PermissionSnapshot(True, "social_reply"),
        mode_modifiers=(),
        memory_event_ids=("m1", "m2"),
    )
    assert decision.willingness is expected


def test_member_chorus_does_not_borrow_current_sender_relationship():
    decision = StancePolicy().decide(
        _member_chorus(),
        actor_relationship=_relationship("u2", warmth=100, play_acceptance=100),
        subject_relationship=None,
        culture_patterns=("light_member_banter",),
        permission=PermissionSnapshot(True, "social_reply"),
        mode_modifiers=(),
        memory_event_ids=(),
    )
    assert decision.willingness is Willingness.UNWILLING


def _other_chorus(*, repetition_count=2, tone="SAFE_BANTER"):
    return SocialScene.create(
        scene_kind="group_chorus",
        target_scope="GROUP",
        target_id=None,
        literal_subject="好无聊啊",
        user_move="chorus_other",
        continuity_event_ids=("m1", "m2", "m3")[: max(2, repetition_count)],
        repetition_count=repetition_count,
        chorus_target="OTHER",
        chorus_target_id=None,
        chorus_chain_id="chorus:bored",
        chorus_payload="好无聊啊",
        chorus_event_ids=("m1", "m2", "m3")[: max(2, repetition_count)],
        chorus_participant_ids=("u1", "u2", "u3")[: max(2, repetition_count)],
        chorus_tone=tone,
        confidence=0.95,
    )


def test_other_chorus_stays_willing_even_when_chain_has_three_participants():
    decision = StancePolicy().decide(
        _other_chorus(repetition_count=3),
        actor_relationship=_relationship("u3"),
        subject_relationship=None,
        culture_patterns=(),
        permission=PermissionSnapshot(True, "social_reply"),
        mode_modifiers=(),
        memory_event_ids=("m1", "m2", "m3"),
    )
    assert decision.willingness is Willingness.WILLING
    assert decision.attitude is Attitude.AMUSED


def test_non_chorus_repetition_still_uses_spam_gate():
    decision = StancePolicy().decide(
        _scene("repeated_ask", repetition_count=3),
        actor_relationship=_relationship(),
        subject_relationship=None,
        culture_patterns=(),
        permission=PermissionSnapshot(True, "social_reply"),
        mode_modifiers=(),
        memory_event_ids=("m1",),
    )
    assert decision.willingness is Willingness.UNWILLING
    assert decision.boundary is Boundary.FIRM
