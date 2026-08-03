"""TriggerRouter wake contract tests.

Contract:
- platform @ / reply → NATIVE_DIRECT
- sentence-initial alias → ALIAS_DIRECT (no colloquial whitelist)
- mid-sentence alias → ALIAS_MENTION
- 叫/喊/问问 + alias → ALIAS_DIRECT
"""

from groupmate.models import ChatMessage, MessageOrigin, TriggerKind
from groupmate.engine.triggers import TriggerRouter


def build_router(aliases=("爱弥斯", "小爱", "飞行雪绒")):
    return TriggerRouter(aliases=aliases)


def poke_message(**overrides):
    values = dict(
        message_id="poke-1",
        group_id="g1",
        sender_id="u1",
        sender_name="Alice",
        text="",
        timestamp=100,
        segment_types=("poke",),
        origin=MessageOrigin.SYSTEM_SYNTHETIC,
        metadata={
            "interaction_kind": "poke",
            "target_id": "bot",
            "source_adapter": "aiocqhttp_poke",
        },
    )
    values.update(overrides)
    return ChatMessage(**values)


def test_existing_command_is_bypassed(message_factory):
    result = build_router().classify(message_factory(text="help", is_command=True))

    assert result.kind is TriggerKind.COMMAND


def test_native_at_is_native_direct(message_factory):
    router = TriggerRouter(aliases=("爱弥斯", "小爱"))
    result = router.classify(message_factory(text="在吗", mentions_bot=True))

    assert result.kind is TriggerKind.NATIVE_DIRECT


def test_reply_to_bot_is_native_direct(message_factory):
    result = build_router().classify(
        message_factory(text="接着说", reply_to_bot=True)
    )

    assert result.kind is TriggerKind.NATIVE_DIRECT


def test_exact_alias_is_direct(message_factory):
    router = build_router()

    assert router.classify(message_factory(text="爱弥斯")).kind is TriggerKind.ALIAS_DIRECT
    assert router.classify(message_factory(text="小爱")).kind is TriggerKind.ALIAS_DIRECT


def test_prefix_alias_is_direct_without_colloquial_list(message_factory):
    router = build_router()
    cases = (
        "小爱，在吗",
        "小爱同学",
        "爱弥斯你在吗",
        "爱弥斯你在不",
        "爱弥斯在么",
        "爱弥斯bot",
        "爱弥斯 bot",
        "爱弥斯帮我看看这题怎么做",
        "爱弥斯你觉得呢",
        "小爱是不是挺难调的",
    )
    for text in cases:
        result = router.classify(message_factory(text=text))
        assert result.kind is TriggerKind.ALIAS_DIRECT, text


def test_leading_plain_text_at_is_copied_at(message_factory):
    router = build_router()
    result = router.classify(message_factory(text="@爱弥斯帮我看下"))

    assert result.kind is TriggerKind.COPIED_AT
    assert result.alias == "爱弥斯"


def test_copied_at_example_like_screenshot(message_factory):
    router = build_router(aliases=("小维", "爱弥斯"))
    result = router.classify(
        message_factory(text="@小维 xw压缩数据是怎么用的", mentions_bot=False)
    )

    assert result.kind is TriggerKind.COPIED_AT
    assert result.alias == "小维"


def test_real_platform_at_still_native_direct(message_factory):
    result = build_router().classify(
        message_factory(text="@爱弥斯帮我看下", mentions_bot=True)
    )

    assert result.kind is TriggerKind.NATIVE_DIRECT


def test_longer_alias_wins_over_shorter_prefix(message_factory):
    router = build_router(aliases=("爱弥斯", "爱"))
    result = router.classify(message_factory(text="爱弥斯你在不"))

    assert result.kind is TriggerKind.ALIAS_DIRECT
    assert result.alias == "爱弥斯"


def test_mid_sentence_alias_is_soft_mention(message_factory):
    router = build_router()
    cases = (
        "我觉得爱弥斯挺难调的",
        "今天群里爱弥斯又没说话",
        "把爱弥斯喊出来吧",
    )
    for text in cases:
        result = router.classify(message_factory(text=text))
        assert result.kind is TriggerKind.ALIAS_MENTION, text


def test_explicit_summon_verb_is_direct(message_factory):
    router = build_router()

    assert router.classify(message_factory(text="喊喊爱弥斯")).kind is TriggerKind.ALIAS_DIRECT
    assert router.classify(message_factory(text="叫小爱出来")).kind is TriggerKind.ALIAS_DIRECT
    assert router.classify(message_factory(text="问问爱弥斯")).kind is TriggerKind.ALIAS_DIRECT


def test_ordinary_message_is_candidate(message_factory):
    result = build_router().classify(message_factory(text="今天天气真好"))

    assert result.kind is TriggerKind.CANDIDATE


def test_bot_and_empty_messages_are_ignored(message_factory):
    router = build_router()

    assert router.classify(message_factory(is_bot=True)).kind is TriggerKind.IGNORE
    assert router.classify(message_factory(text="", segment_types=())).kind is TriggerKind.IGNORE


def test_strict_synthetic_poke_is_host_interaction():
    result = build_router().classify(poke_message())

    assert result.kind is TriggerKind.HOST_INTERACTION
    assert result.reason == "host_interaction:poke"


def test_unknown_or_mismatched_synthetic_interaction_is_ignored():
    router = build_router()
    unknown = poke_message(metadata={"interaction_kind": "wave"})
    mismatched = poke_message(segment_types=("text", "poke"))

    assert router.classify(unknown).kind is TriggerKind.IGNORE
    assert router.classify(mismatched).kind is TriggerKind.IGNORE
