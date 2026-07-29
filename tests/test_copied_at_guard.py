from groupmate.engine.copied_at import copied_at_tip, is_copied_at
from groupmate.models import TriggerKind


def test_copied_at_tip_uses_aemeath_style_and_alias():
    assert copied_at_tip("爱弥斯") == (
        "复制出来的 @ 不算数哦，要叫爱弥斯的话，用真正的 @。"
    )


def test_copied_at_tip_defaults_to_aemeath_name():
    assert copied_at_tip("") == (
        "复制出来的 @ 不算数哦，要叫爱弥斯的话，用真正的 @。"
    )


def test_copied_at_guard_only_matches_copied_at_trigger():
    assert is_copied_at(TriggerKind.COPIED_AT) is True
    assert is_copied_at(TriggerKind.NATIVE_DIRECT) is False
    assert is_copied_at(TriggerKind.ALIAS_DIRECT) is False
