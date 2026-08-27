from __future__ import annotations

from groupmate.social_runtime.profile.contracts import ProfileObservation
from groupmate.social_runtime.profile.speech_style import (
    MemberStyleEvidence,
    MemberStyleEvidencePolicy,
    MemberStyleMaturity,
    MemberStyleSetting,
)


def _evidence(index: int, *, day: int, scene: str, short: bool = False):
    return MemberStyleEvidence(
        event_id=f"event-{index}",
        group_id="group-1",
        member_id="member-1",
        text="好" if short else f"这是第 {index} 条能够体现说话结构的消息",
        occurred_at=1_800_000_000 + day * 86_400 + index,
        scene_type=scene,
        short_reaction=short,
    )


def _observation(text: str, **payload) -> ProfileObservation:
    return ProfileObservation(
        event_id="event-1",
        persona_id="persona",
        group_id="group-1",
        actor_id="member-1",
        payload={"text": text, **payload},
        occurred_at=1_800_000_000,
    )


def test_member_style_setting_is_disabled_by_default():
    setting = MemberStyleSetting.disabled("group-1", "member-1")

    assert setting.enabled is False
    assert setting.enabled_at == 0
    assert setting.version == 0


def test_maturity_requires_volume_days_and_scene_diversity():
    scenes = ("answer", "banter", "care")
    evidence = tuple(
        _evidence(index, day=index % 5, scene=scenes[index % 3])
        for index in range(40)
    )

    maturity = MemberStyleMaturity.from_evidence(evidence)

    assert maturity.ready is True
    assert maturity.eligible_message_count == 40
    assert maturity.active_day_count == 5
    assert maturity.scene_types == scenes


def test_short_reactions_cannot_dominate_a_ready_style():
    scenes = ("answer", "banter", "care")
    evidence = tuple(
        _evidence(
            index,
            day=index % 5,
            scene=scenes[index % 3],
            short=index >= 20,
        )
        for index in range(40)
    )

    maturity = MemberStyleMaturity.from_evidence(evidence)

    assert maturity.ready is False
    assert maturity.substantive_message_count == 20


def test_evidence_policy_rejects_commands_forwarded_chorus_and_sensitive_text():
    policy = MemberStyleEvidencePolicy()

    assert policy.select(_observation("bq", social_eligible=False)) is None
    assert policy.select(_observation("转发内容", segments=[{"type": "forward"}])) is None
    assert policy.select(_observation("跟着大家复读", chorus_chain_id="chain-1")) is None
    assert policy.select(_observation("手机号 13800138000")) is None


def test_evidence_policy_keeps_authored_text_and_marks_short_reaction():
    policy = MemberStyleEvidencePolicy()

    evidence = policy.select(_observation("笑死"))

    assert evidence is not None
    assert evidence.member_id == "member-1"
    assert evidence.scene_type == "banter"
    assert evidence.short_reaction is True
