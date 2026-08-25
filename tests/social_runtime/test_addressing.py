from __future__ import annotations

import pytest

from groupmate.social_runtime.addressing import PersonaAddressResolver


@pytest.mark.parametrize(
    ("text", "addressed", "kind", "remainder"),
    (
        ("小爱", True, "PURE_ALIAS", ""),
        ("小爱呢", True, "ALIAS_PREFIX", "呢"),
        ("小爱 说话", True, "ALIAS_PREFIX", "说话"),
        ("爱弥斯 bq 开心", True, "ALIAS_PREFIX", "bq 开心"),
        ("你怎么看，小爱", True, "ALIAS_SUFFIX", "你怎么看"),
        ("我觉得小爱这个名字不错", False, "NONE", "我觉得小爱这个名字不错"),
        ("这是小爱情节", False, "NONE", "这是小爱情节"),
    ),
)
def test_resolver_distinguishes_calls_from_body_mentions(
    text, addressed, kind, remainder
):
    result = PersonaAddressResolver("爱弥斯", ("小爱",)).resolve_text(text)

    assert result.addressed_to_bot is addressed
    assert result.address_kind == kind
    assert result.address_remainder == remainder


def test_platform_at_and_reply_are_high_confidence_without_alias_text():
    resolver = PersonaAddressResolver("爱弥斯", ("小爱",))

    at = resolver.resolve(text="早", mentions_bot=True, reply_to_bot=False)
    reply = resolver.resolve(text="然后呢", mentions_bot=False, reply_to_bot=True)

    assert (at.address_kind, reply.address_kind) == ("AT", "REPLY")
    assert at.address_confidence == reply.address_confidence == "HIGH"


def test_new_alias_is_only_recorded_as_a_candidate():
    resolver = PersonaAddressResolver("爱弥斯", ("小爱",))

    result = resolver.resolve_text("小爱以后叫你爱酱")

    assert result.addressed_to_bot is True
    assert result.alias_candidate == "爱酱"
    assert "爱酱" not in resolver.names


def test_alias_prefix_requires_a_bounded_call_boundary():
    result = PersonaAddressResolver("爱弥斯", ("小爱",)).resolve_text("小爱心情不错")

    assert result.addressed_to_bot is False
    assert result.address_kind == "NONE"
