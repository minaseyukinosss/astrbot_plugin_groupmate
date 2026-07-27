"""AddresseeResolver：reply/mention/称呼/邻接/转述/歧义。"""

from __future__ import annotations

from groupmate.core.addressee import AddresseeResolver
from groupmate.models import (
    AddresseeKind,
    ChatMessage,
    TopicSnapshot,
    TriggerKind,
)


def _msg(**overrides):
    values = {
        "message_id": "m1",
        "group_id": "g1",
        "sender_id": "u1",
        "sender_name": "Alice",
        "text": "你好",
        "timestamp": 100,
    }
    values.update(overrides)
    return ChatMessage(**values)


def _topic(*messages):
    return TopicSnapshot(
        topic_id="t1",
        group_id="g1",
        messages=tuple(messages),
        created_at=messages[0].timestamp,
        updated_at=messages[-1].timestamp,
    )


def test_reply_chain_targets_quoted_user():
    quoted = _msg(message_id="m0", sender_id="u2", sender_name="Bob", text="在吗")
    reply = _msg(
        message_id="m1",
        sender_id="u1",
        text="回你",
        reply_to_message_id="m0",
        timestamp=101,
    )
    decision = AddresseeResolver().resolve(
        _topic(quoted, reply), TriggerKind.ALIAS_DIRECT
    )
    assert decision.reply_audience.kind is AddresseeKind.USER
    assert decision.reply_audience.target_user_ids == ("u1",)
    assert "reply_chain" in decision.social_target.reason_codes or (
        decision.social_target.target_user_ids == ("u1",)
    )


def test_platform_mention_single_user():
    message = _msg(
        text="@Bob 来一下",
        mentioned_user_ids=("u2",),
    )
    decision = AddresseeResolver().resolve(
        _topic(message), TriggerKind.CANDIDATE, bot_id="bot"
    )
    assert decision.reply_audience.target_user_ids == ("u2",)
    assert decision.social_target.target_user_ids == ("u1",)


def test_multi_mention_is_ambiguous_for_social():
    message = _msg(
        text="@Bob @Carol",
        mentioned_user_ids=("u2", "u3"),
    )
    decision = AddresseeResolver().resolve(
        _topic(message), TriggerKind.CANDIDATE, bot_id="bot"
    )
    assert decision.social_target.kind is AddresseeKind.AMBIGUOUS
    assert "multi_mention" in decision.social_target.reason_codes


def test_leading_address_maps_configured_nickname():
    message = _msg(sender_id="u9", sender_name="陌生人", text="小A，过来")
    decision = AddresseeResolver().resolve(
        _topic(message),
        TriggerKind.CANDIDATE,
        relationships={"u1": ("闺蜜", "小A")},
    )
    assert decision.reply_audience.target_user_ids == ("u1",)


def test_adjacent_qa_targets_previous_asker():
    ask = _msg(message_id="m0", sender_id="u2", sender_name="Bob", text="怎么弄？")
    answer = _msg(
        message_id="m1",
        sender_id="u1",
        sender_name="Alice",
        text="先重启",
        timestamp=101,
    )
    decision = AddresseeResolver().resolve(
        _topic(ask, answer), TriggerKind.CANDIDATE
    )
    assert decision.reply_audience.target_user_ids == ("u2",)
    assert "adjacent_qa" in decision.reply_audience.reason_codes


def test_recount_keeps_reply_and_social_on_a_memory_ambiguous_for_b():
    prior = _msg(message_id="m0", sender_id="u2", sender_name="Bob", text="我明天考试")
    recount = _msg(
        message_id="m1",
        sender_id="u1",
        sender_name="Alice",
        text="他说Bob明天考试",
        timestamp=101,
        mentioned_user_ids=("u2",),
    )
    decision = AddresseeResolver().resolve(
        _topic(prior, recount), TriggerKind.ALIAS_DIRECT, bot_id="bot"
    )
    assert decision.reply_audience.target_user_ids[0] == "u1"
    assert decision.social_target.target_user_ids == ("u1",)
    assert decision.memory_subject.kind is AddresseeKind.AMBIGUOUS
    assert "no_personal_memory" in decision.memory_subject.reason_codes
    assert "u2" in decision.memory_subject.target_user_ids


def test_latest_speaker_fallback():
    message = _msg(text="随便聊聊")
    decision = AddresseeResolver().resolve(
        _topic(message), TriggerKind.CANDIDATE
    )
    assert decision.social_target.target_user_ids == ("u1",)
    assert "latest_speaker" in decision.reply_audience.reason_codes or (
        decision.social_target.kind is AddresseeKind.USER
    )
