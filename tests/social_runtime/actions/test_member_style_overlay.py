from groupmate.social_runtime.actions.member_style import (
    IdentityImitationGuard,
    MemberStyleOverlayBuilder,
)
from tests.social_runtime.profile.test_style_repository import _style


def test_overlay_contains_qualitative_style_without_evidence_ids():
    overlay = MemberStyleOverlayBuilder().build(
        _style(), target_display_name="阿甲", expires_at=200
    )

    assert overlay.target_display_name == "阿甲"
    assert overlay.style_version == 1
    assert any("结论在前" in item for item in overlay.directives)
    assert "event-1" not in repr(overlay)


def test_guard_rejects_claiming_target_identity():
    overlay = MemberStyleOverlayBuilder().build(
        _style(), target_display_name="阿甲", expires_at=200
    )

    violations = IdentityImitationGuard().review(
        "我就是阿甲，本人来了。", overlay=overlay
    )

    assert "imitation_target_identity_claim" in violations


def test_guard_keeps_aemeath_identity_statement():
    overlay = MemberStyleOverlayBuilder().build(
        _style(), target_display_name="阿甲", expires_at=200
    )

    assert IdentityImitationGuard().review(
        "说话像了一点而已，我还是爱弥斯。", overlay=overlay
    ) == ()


def test_guard_does_not_treat_aemeath_own_first_person_life_as_borrowed():
    overlay = MemberStyleOverlayBuilder().build(
        _style(), target_display_name="阿甲", expires_at=200
    )

    assert IdentityImitationGuard().review(
        "我上学时也碰到过这种事。", overlay=overlay
    ) == ()
