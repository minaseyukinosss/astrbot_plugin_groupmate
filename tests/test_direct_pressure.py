from groupmate.engine.direct_pressure import (
    DirectAddressPressureLevel,
    DirectAddressPressureTracker,
)
from groupmate.models import ChatMessage, MessageOrigin, TriggerKind


def message(
    text="爱弥斯",
    *,
    timestamp=100,
    sender_id="u1",
    mentions_bot=False,
    reply_to_bot=False,
):
    return ChatMessage(
        message_id=str(timestamp),
        group_id="g1",
        sender_id=sender_id,
        sender_name="Alice",
        text=text,
        timestamp=timestamp,
        mentions_bot=mentions_bot,
        reply_to_bot=reply_to_bot,
    )


def tracker():
    return DirectAddressPressureTracker(
        window_seconds=600,
        nudge_count=2,
        pester_count=3,
    )


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


def test_repeated_bare_alias_direct_escalates_to_pester():
    pressure = tracker()

    first = pressure.observe(
        "aemeath",
        message(timestamp=100),
        TriggerKind.ALIAS_DIRECT,
        now=100,
        aliases=("爱弥斯",),
    )
    second = pressure.observe(
        "aemeath",
        message(timestamp=120),
        TriggerKind.ALIAS_DIRECT,
        now=120,
        aliases=("爱弥斯",),
    )
    third = pressure.observe(
        "aemeath",
        message(timestamp=140),
        TriggerKind.ALIAS_DIRECT,
        now=140,
        aliases=("爱弥斯",),
    )

    assert first.level is DirectAddressPressureLevel.NORMAL
    assert second.level is DirectAddressPressureLevel.NUDGE
    assert third.level is DirectAddressPressureLevel.PESTER


def test_contentful_direct_resets_pressure():
    pressure = tracker()
    pressure.observe(
        "aemeath",
        message(timestamp=100),
        TriggerKind.ALIAS_DIRECT,
        now=100,
        aliases=("爱弥斯",),
    )
    pressure.observe(
        "aemeath",
        message(timestamp=120),
        TriggerKind.ALIAS_DIRECT,
        now=120,
        aliases=("爱弥斯",),
    )

    state = pressure.observe(
        "aemeath",
        message("爱弥斯，这个怎么弄？", timestamp=130),
        TriggerKind.ALIAS_DIRECT,
        now=130,
        aliases=("爱弥斯",),
    )

    assert state.level is DirectAddressPressureLevel.NORMAL
    assert state.count == 0
    assert state.reason_codes == ("pressure_reset_contentful",)


def test_real_native_at_counts_but_reply_to_bot_does_not():
    pressure = tracker()

    native = pressure.observe(
        "aemeath",
        message("@爱弥斯", timestamp=100, mentions_bot=True),
        TriggerKind.NATIVE_DIRECT,
        now=100,
        aliases=("爱弥斯",),
    )
    reply = pressure.observe(
        "aemeath",
        message("嗯？", timestamp=120, reply_to_bot=True),
        TriggerKind.NATIVE_DIRECT,
        now=120,
        aliases=("爱弥斯",),
    )

    assert native.count == 1
    assert reply.count == 0
    assert reply.reason_codes == ("pressure_excluded_reply",)


def test_copied_at_and_continuation_are_excluded_from_pressure():
    pressure = tracker()

    copied = pressure.observe(
        "aemeath",
        message("@爱弥斯", timestamp=100),
        TriggerKind.COPIED_AT,
        now=100,
        aliases=("爱弥斯",),
    )
    continuation = pressure.observe(
        "aemeath",
        message("然后呢", timestamp=120),
        TriggerKind.CONTINUATION,
        now=120,
        aliases=("爱弥斯",),
    )

    assert copied.count == 0
    assert copied.reason_codes == ("pressure_excluded", "copied_at")
    assert continuation.count == 0
    assert continuation.reason_codes == ("pressure_excluded", "continuation")


def test_pressure_is_isolated_per_sender_and_expires_by_window():
    pressure = tracker()
    pressure.observe(
        "aemeath",
        message(timestamp=100, sender_id="u1"),
        TriggerKind.ALIAS_DIRECT,
        now=100,
        aliases=("爱弥斯",),
    )

    other = pressure.observe(
        "aemeath",
        message(timestamp=120, sender_id="u2"),
        TriggerKind.ALIAS_DIRECT,
        now=120,
        aliases=("爱弥斯",),
    )
    expired = pressure.observe(
        "aemeath",
        message(timestamp=800, sender_id="u1"),
        TriggerKind.ALIAS_DIRECT,
        now=800,
        aliases=("爱弥斯",),
    )

    assert other.count == 1
    assert expired.count == 1


def test_tracker_can_apply_group_policy_thresholds():
    pressure = tracker()
    pressure.configure(
        window_seconds=300,
        nudge_count=3,
        pester_count=4,
    )

    first = pressure.observe(
        "aemeath",
        message(timestamp=100),
        TriggerKind.ALIAS_DIRECT,
        now=100,
        aliases=("爱弥斯",),
    )
    second = pressure.observe(
        "aemeath",
        message(timestamp=120),
        TriggerKind.ALIAS_DIRECT,
        now=120,
        aliases=("爱弥斯",),
    )

    assert first.level is DirectAddressPressureLevel.NORMAL
    assert second.level is DirectAddressPressureLevel.NORMAL


def test_pressure_key_includes_persona():
    pressure = tracker()
    pressure.observe(
        "aemeath",
        message(timestamp=100),
        TriggerKind.ALIAS_DIRECT,
        now=100,
        aliases=("爱弥斯",),
    )

    future = pressure.observe(
        "future",
        message(timestamp=120),
        TriggerKind.ALIAS_DIRECT,
        now=120,
        aliases=("新人格",),
    )

    assert future.level is DirectAddressPressureLevel.NORMAL
    assert future.count == 1


def test_repeated_host_interactions_share_direct_pressure_window():
    pressure = tracker()

    states = tuple(
        pressure.observe(
            "aemeath",
            poke_message(message_id="poke-{}".format(index), timestamp=timestamp),
            TriggerKind.HOST_INTERACTION,
            now=timestamp,
            aliases=("爱弥斯",),
        )
        for index, timestamp in enumerate((100, 120, 140), start=1)
    )

    assert tuple(state.count for state in states) == (1, 2, 3)
    assert states[-1].level is DirectAddressPressureLevel.PESTER
