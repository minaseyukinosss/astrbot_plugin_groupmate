import asyncio

from groupmate.core.addressee import AddresseeResolver
from groupmate.memory.store import SQLiteMemoryStore
from groupmate.models import SelfCommitmentStatus, TopicSnapshot, TriggerKind
from groupmate.social.commitments import SelfCommitmentWriter
from groupmate.social.reminder_infer import (
    acceptance_fallback_for_request,
    infer_timed_reminder_commitment,
    infer_timed_reminder_from_topic,
    infer_timed_reminder_request,
    latest_user_text,
    looks_like_premature_reminder_delivery,
    looks_like_reminder_cancel,
    looks_like_timed_reminder_request,
    looks_like_timed_reminder_continuity,
    parse_relative_offset_seconds,
    recover_due_at,
    reminder_task_from_summary,
)


class NoneCommitmentModel:
    async def extract_self_commitment(self, **kwargs):
        del kwargs
        return {"action": "NONE", "confidence": 0.0}


class BrokenDueReminderModel:
    async def extract_self_commitment(self, **kwargs):
        del kwargs
        return {
            "action": "OPEN",
            "summary": "提醒交材料",
            "evidence_quote": "1分钟倒计时开始",
            "required_capability": "",
            "fulfillment_mode": "reminder",
            "due_at": None,
            "confidence": 0.97,
        }


def direct_targeting(topic):
    return AddresseeResolver().resolve(
        topic, TriggerKind.ALIAS_DIRECT, aliases=("爱弥斯", "小爱")
    )


def test_parse_relative_offsets():
    assert parse_relative_offset_seconds("1分钟后提醒我交材料") == 60
    assert parse_relative_offset_seconds("好嘞，1分钟倒计时开始哦") == 60
    assert parse_relative_offset_seconds("半小时后叫我") == 1800
    assert parse_relative_offset_seconds("半分钟后提醒我") == 30
    assert parse_relative_offset_seconds("2小时后") == 7200
    assert parse_relative_offset_seconds("普通聊天") is None
    assert (
        parse_relative_offset_seconds("我都等了5分钟了，1小时后再提醒我")
        == 3600
    )


def test_infer_timed_reminder_from_countdown_acceptance():
    now = 1_700_000_000
    payload = infer_timed_reminder_commitment(
        user_text="小爱，1分钟后提醒我交材料",
        reply_text="好嘞，1分钟倒计时开始哦",
        now=now,
    )
    assert payload is not None
    assert payload["action"] == "OPEN"
    assert payload["fulfillment_mode"] == "reminder"
    assert payload["due_at"] == now + 60
    assert "交材料" in payload["summary"]
    assert payload["evidence_quote"] in "好嘞，1分钟倒计时开始哦"


def test_infer_rejects_vague_offer_without_timed_request():
    assert (
        infer_timed_reminder_commitment(
            user_text="今天好累",
            reply_text="有事叫我",
            now=100,
        )
        is None
    )


def test_writer_opens_reminder_when_extractor_returns_none(
    tmp_path, message_factory
):
    store = SQLiteMemoryStore(tmp_path / "reminder-heuristic.db")
    try:
        topic = TopicSnapshot(
            "t1",
            "g",
            (
                message_factory(
                    message_id="m1",
                    sender_id="u1",
                    sender_name="复读斥候",
                    text="小爱，1分钟后提醒我交材料",
                    timestamp=100,
                    mentions_bot=True,
                ),
            ),
            100,
            100,
        )
        writer = SelfCommitmentWriter(
            store, NoneCommitmentModel(), persona_id="aemeath"
        )
        item = asyncio.run(
            writer.process(
                topic,
                direct_targeting(topic),
                decision_id="d-reminder",
                now=1000,
                reply_text="好嘞，1分钟倒计时开始哦",
            )
        )
        assert item is not None
        assert item.status is SelfCommitmentStatus.PENDING
        assert item.fulfillment_mode == "reminder"
        assert item.due_at == 1060
        assert item.next_attempt_at == 1060
        assert item.extractor_version == "reminder-heuristic-v1"
        assert "交材料" in item.summary
    finally:
        store.close()


def test_writer_recovers_missing_due_at_for_reminder_payload(
    tmp_path, message_factory
):
    store = SQLiteMemoryStore(tmp_path / "reminder-due-recover.db")
    try:
        topic = TopicSnapshot(
            "t1",
            "g",
            (
                message_factory(
                    message_id="m1",
                    sender_id="u1",
                    sender_name="复读斥候",
                    text="爱弥斯，10分钟后提醒我交材料",
                    timestamp=100,
                    mentions_bot=True,
                ),
            ),
            100,
            100,
        )
        writer = SelfCommitmentWriter(
            store, BrokenDueReminderModel(), persona_id="aemeath"
        )
        item = asyncio.run(
            writer.process(
                topic,
                direct_targeting(topic),
                decision_id="d-due",
                now=2000,
                reply_text="好，1分钟倒计时开始；不对，是10分钟，开始啦",
            )
        )
        # reply contains "1分钟" first; recover prefers user request (10 minutes)
        assert recover_due_at(
            user_text="爱弥斯，10分钟后提醒我交材料",
            reply_text="好，1分钟倒计时开始；不对，是10分钟，开始啦",
            now=2000,
        ) == 2600
        assert item is not None
        assert item.fulfillment_mode == "reminder"
        assert item.due_at == 2600
    finally:
        store.close()


def test_premature_delivery_detected_for_immediate_fulfillment():
    assert looks_like_premature_reminder_delivery(
        user_text="小爱，1分钟后提醒我交材料",
        reply_text="交材料了",
    )
    assert looks_like_premature_reminder_delivery(
        user_text="小爱，1分钟后提醒我交材料",
        reply_text="到时间了，交材料",
    )
    assert not looks_like_premature_reminder_delivery(
        user_text="小爱，1分钟后提醒我交材料",
        reply_text="好嘞，1分钟倒计时开始哦",
    )
    assert acceptance_fallback_for_request("小爱，1分钟后提醒我交材料") == (
        "好嘞，1分钟倒计时开始哦"
    )
    assert not looks_like_premature_reminder_delivery(
        user_text="小爱，1分钟后提醒我交材料",
        reply_text="该吃饭了",
    )
    assert not looks_like_timed_reminder_request("我们1分钟倒计时开始")
    assert looks_like_timed_reminder_continuity(
        "复读斥候要求小爱在1分钟后提醒自己交材料",
        "小爱，1分钟后提醒我交材料",
    )
    assert not looks_like_timed_reminder_continuity(
        "复读斥候表示考完试后告诉对方结果",
        "考完试告诉你结果",
    )
    assert reminder_task_from_summary("提醒交材料") == "交材料"
    assert looks_like_reminder_cancel("算了，不用提醒我了")
    assert looks_like_reminder_cancel("不用提醒我了")
    assert looks_like_reminder_cancel("取消提醒")
    assert not looks_like_reminder_cancel("算了")
    assert looks_like_reminder_cancel("算了", has_open_reminder=True)


def test_latest_user_text_skips_trailing_bot_projection(message_factory):
    topic = TopicSnapshot(
        "t-bot-last",
        "g",
        (
            message_factory(
                message_id="m1",
                text="算了，不用提醒我了",
                timestamp=110,
            ),
            message_factory(
                message_id="bot",
                sender_id="__bot__",
                sender_name="爱弥斯",
                text="两分钟倒计时开始啦 到点我喊你",
                timestamp=101,
                is_bot=True,
            ),
        ),
        100,
        110,
    )
    assert latest_user_text(topic) == "算了，不用提醒我了"
    assert infer_timed_reminder_from_topic(topic, now=200) is None


def test_countdown_game_is_not_a_timed_reminder():
    assert infer_timed_reminder_request("我们1分钟倒计时开始") is None


def test_absolute_tomorrow_morning_reminder():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("Asia/Shanghai")
    now = int(datetime(2026, 8, 13, 10, 0, tzinfo=zone).timestamp())
    parsed = infer_timed_reminder_request("明天早上八点提醒我交材料", now=now)
    assert parsed is not None
    due = datetime.fromtimestamp(parsed.due_at(now), zone)
    assert (due.year, due.month, due.day, due.hour, due.minute) == (
        2026,
        8,
        14,
        8,
        0,
    )


def test_infer_from_split_turns(message_factory):
    topic = TopicSnapshot(
        "t-split",
        "g",
        (
            message_factory(
                message_id="m0",
                sender_id="u1",
                text="帮我记一下",
                timestamp=100,
                mentions_bot=True,
            ),
            message_factory(
                message_id="m1",
                sender_id="u1",
                text="1分钟后交材料",
                timestamp=101,
                mentions_bot=True,
            ),
        ),
        100,
        101,
    )
    parsed = infer_timed_reminder_from_topic(topic, now=1000)
    assert parsed is not None
    assert parsed.offset_seconds == 60
    assert "交材料" in parsed.task_phrase


def test_infer_from_topic_does_not_stitch_cancel_over_request(message_factory):
    topic = TopicSnapshot(
        "t-cancel-cont",
        "g",
        (
            message_factory(
                message_id="m0",
                sender_id="u1",
                text="小爱，2分钟后提醒我交材料",
                timestamp=100,
                mentions_bot=True,
            ),
            message_factory(
                message_id="bot-1",
                sender_id="__bot__",
                sender_name="爱弥斯",
                text="行 两分钟倒计时开始啦 到点叫你",
                timestamp=101,
                is_bot=True,
            ),
            message_factory(
                message_id="m-cancel",
                sender_id="u1",
                text="算了，不用提醒我了",
                timestamp=110,
                mentions_bot=False,
            ),
        ),
        100,
        110,
    )
    assert infer_timed_reminder_from_topic(topic, now=1110) is None


class WrongDueReminderModel:
    async def extract_self_commitment(self, **kwargs):
        del kwargs
        return {
            "action": "OPEN",
            "summary": "提醒交材料",
            "evidence_quote": "嗯我知道了交材料的事",
            "required_capability": "",
            "fulfillment_mode": "reminder",
            "due_at": 2000,
            "confidence": 0.97,
        }


def test_writer_overrides_wrong_model_due_at(tmp_path, message_factory):
    store = SQLiteMemoryStore(tmp_path / "reminder-wrong-due.db")
    try:
        topic = TopicSnapshot(
            "t1",
            "g",
            (
                message_factory(
                    message_id="m1",
                    sender_id="u1",
                    sender_name="复读斥候",
                    text="小爱，10分钟后提醒我交材料",
                    timestamp=100,
                    mentions_bot=True,
                ),
            ),
            100,
            100,
        )
        writer = SelfCommitmentWriter(
            store, WrongDueReminderModel(), persona_id="aemeath"
        )
        item = asyncio.run(
            writer.process(
                topic,
                direct_targeting(topic),
                decision_id="d-wrong-due",
                now=2000,
                reply_text="嗯我知道了交材料的事",
            )
        )
        assert item is not None
        assert item.fulfillment_mode == "reminder"
        assert item.due_at == 2600
    finally:
        store.close()


def test_writer_withdraws_open_reminder_on_cancel(tmp_path, message_factory):
    store = SQLiteMemoryStore(tmp_path / "reminder-cancel.db")
    try:
        topic = TopicSnapshot(
            "t1",
            "g",
            (
                message_factory(
                    message_id="m1",
                    sender_id="u1",
                    sender_name="复读斥候",
                    text="小爱，1分钟后提醒我交材料",
                    timestamp=100,
                    mentions_bot=True,
                ),
            ),
            100,
            100,
        )
        writer = SelfCommitmentWriter(
            store, NoneCommitmentModel(), persona_id="aemeath"
        )
        opened = asyncio.run(
            writer.process(
                topic,
                direct_targeting(topic),
                decision_id="d-open",
                now=1000,
                reply_text="好嘞，1分钟倒计时开始哦",
            )
        )
        assert opened is not None
        cancel_topic = TopicSnapshot(
            "t2",
            "g",
            (
                message_factory(
                    message_id="m2",
                    sender_id="u1",
                    sender_name="复读斥候",
                    text="算了，不用提醒我了",
                    timestamp=110,
                    mentions_bot=False,
                ),
            ),
            110,
            110,
        )
        cancelled = writer.cancel_open_reminder_for_sender(
            cancel_topic,
            decision_id="d-cancel",
            now=1100,
        )
        assert cancelled is not None
        assert cancelled.status is SelfCommitmentStatus.WITHDRAWN
        assert cancelled.commitment_id == opened.commitment_id
    finally:
        store.close()


class RecordingExtractModel:
    def __init__(self):
        self.calls = 0

    async def extract_self_commitment(self, **kwargs):
        del kwargs
        self.calls += 1
        return {
            "action": "OPEN",
            "summary": "提醒交材料",
            "evidence_quote": "行那就不喊了",
            "required_capability": "",
            "fulfillment_mode": "reminder",
            "due_at": 2000,
            "confidence": 0.99,
        }


def test_writer_does_not_reopen_reminder_on_continuation_cancel(
    tmp_path, message_factory
):
    store = SQLiteMemoryStore(tmp_path / "reminder-cont-cancel.db")
    try:
        request = message_factory(
            message_id="m1",
            sender_id="u1",
            sender_name="复读斥候",
            text="小爱，2分钟后提醒我交材料",
            timestamp=100,
            mentions_bot=True,
        )
        open_topic = TopicSnapshot("t1", "g", (request,), 100, 100)
        extractor = RecordingExtractModel()
        writer = SelfCommitmentWriter(store, extractor, persona_id="aemeath")
        opened = asyncio.run(
            writer.process(
                open_topic,
                direct_targeting(open_topic),
                decision_id="d-open",
                now=1000,
                reply_text="行 两分钟倒计时开始啦 到点叫你",
            )
        )
        assert opened is not None
        extractor.calls = 0
        cancel_topic = TopicSnapshot(
            "t1",
            "g",
            (
                request,
                message_factory(
                    message_id="bot-1",
                    sender_id="__bot__",
                    sender_name="爱弥斯",
                    text="行 两分钟倒计时开始啦 到点叫你",
                    timestamp=101,
                    is_bot=True,
                ),
                message_factory(
                    message_id="m-cancel",
                    sender_id="u1",
                    sender_name="复读斥候",
                    text="算了，不用提醒我了",
                    timestamp=110,
                    mentions_bot=False,
                ),
            ),
            100,
            110,
        )
        result = asyncio.run(
            writer.process(
                cancel_topic,
                direct_targeting(cancel_topic),
                decision_id="d-cancel",
                now=1100,
                reply_text="行那就不喊了",
            )
        )
        assert extractor.calls == 0
        assert result is not None
        assert result.status is SelfCommitmentStatus.WITHDRAWN
        assert result.commitment_id == opened.commitment_id
        open_items = store.list_self_commitments(
            "aemeath",
            group_id="g",
            statuses=(SelfCommitmentStatus.PENDING, SelfCommitmentStatus.IN_PROGRESS),
        )
        assert open_items == []
    finally:
        store.close()