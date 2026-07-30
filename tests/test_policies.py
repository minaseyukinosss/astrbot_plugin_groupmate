from inspect import signature

from groupmate.core.intent import max_chars_for_mode
from groupmate.models import ReplyMode
from groupmate.policies import BehaviorPolicy


def test_behavior_policy_contains_only_focused_internal_policies():
    behavior = BehaviorPolicy()

    assert behavior.participation.direct_pressure_window_seconds == 600
    assert behavior.conversation.continuation_seconds == 90
    assert behavior.reply.max_reply_segments == 2
    assert behavior.resources.open_send_hourly_limit > 0
    assert not hasattr(behavior, "aliases")
    assert not hasattr(behavior, "vision_enabled")
    assert not hasattr(behavior, "v3_scheduler_enabled")


def test_reply_length_comes_from_reply_mode_not_global_policy():
    assert "policy_max" not in signature(max_chars_for_mode).parameters
    assert max_chars_for_mode(ReplyMode.SHORT_SOCIAL) == 60
    assert max_chars_for_mode(ReplyMode.HELP_DETAIL) == 180
