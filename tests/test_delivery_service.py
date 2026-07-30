import asyncio
import json

from groupmate.engine.delivery import DeliveryPlan, DeliveryService
from groupmate.memory.store import SQLiteMemoryStore
from groupmate.models import OutboundKind, OutboundSegment, SendResult
from tests.fakes import FakeClock


class ReceiptPlatform:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def send_segments(
        self, group_id, segments, decision_id, quote_message_id=None
    ):
        del group_id, segments, decision_id, quote_message_id
        self.calls += 1
        return self.result


def plan(decision_id):
    return DeliveryPlan(
        decision_id=decision_id,
        group_id="g",
        segments=("在呢。",),
        delay_seconds=0,
        expires_at=200,
    )


def test_confirmed_delivery_atomically_writes_bot_message(tmp_path):
    async def scenario():
        store = SQLiteMemoryStore(tmp_path / "delivery.db")
        platform = ReceiptPlatform(SendResult.confirmed())
        service = DeliveryService(platform, store, FakeClock(101), persona_id="aemeath", character_name="爱弥斯")
        outcome = await service.deliver(plan("confirmed"))
        row = store.outbox_record("aemeath", "confirmed")
        messages = store.recent_messages("aemeath", "g", 10)
        store.close()
        return outcome, row, messages, platform.calls

    outcome, row, messages, calls = asyncio.run(scenario())
    assert outcome.sent is True
    assert row["status"] == "sent"
    assert row["attempt"] == 1
    assert calls == 1
    assert len(messages) == 1
    assert messages[0].is_bot is True
    assert messages[0].metadata["decision_id"] == "confirmed"


def test_rich_delivery_persists_one_ordered_outbox_and_accurate_bot_message(tmp_path):
    class RichPlatform:
        def __init__(self):
            self.calls = []

        async def send_outbound(
            self, group_id, outbound, decision_id, quote_message_id=None
        ):
            self.calls.append(
                (group_id, tuple(outbound), decision_id, quote_message_id)
            )
            return SendResult.confirmed()

    async def scenario():
        store = SQLiteMemoryStore(tmp_path / "rich.db")
        platform = RichPlatform()
        service = DeliveryService(platform, store, FakeClock(101), persona_id="aemeath", character_name="爱弥斯")
        rich_plan = DeliveryPlan(
            decision_id="rich",
            group_id="g",
            segments=(),
            delay_seconds=0,
            expires_at=200,
            quote_message_id="source-1",
            outbound=(
                OutboundSegment(OutboundKind.TEXT, text="给你看"),
                OutboundSegment(
                    OutboundKind.IMAGE,
                    media_id="result-1",
                    media_ref="https://example.test/result.png",
                ),
            ),
        )
        outcome = await service.deliver(rich_plan)
        row = store.outbox_record("aemeath", "rich")
        messages = store.recent_messages("aemeath", "g", 10)
        row_count = store._db.execute(
            "SELECT COUNT(*) FROM outbox WHERE decision_id='rich'"
        ).fetchone()[0]
        store.close()
        return outcome, row, messages, platform.calls, row_count

    outcome, row, messages, calls, row_count = asyncio.run(scenario())

    assert outcome.sent is True
    assert row_count == 1
    assert row["status"] == "sent"
    assert [item["kind"] for item in json.loads(row["outbound_json"])] == [
        "text",
        "image",
    ]
    assert calls[0][3] == "source-1"
    assert messages[0].text == "给你看"
    assert messages[0].segment_types == ("text", "image")
    assert messages[0].image_urls == ("https://example.test/result.png",)
    assert messages[0].metadata["media_ids"] == ["result-1"]


def test_unknown_delivery_is_terminal_and_not_retried(tmp_path):
    async def scenario():
        store = SQLiteMemoryStore(tmp_path / "unknown.db")
        platform = ReceiptPlatform(SendResult.unknown())
        service = DeliveryService(platform, store, FakeClock(101), persona_id="aemeath")
        first = await service.deliver(plan("unknown"))
        second = await service.deliver(plan("unknown"))
        row = store.outbox_record("aemeath", "unknown")
        messages = store.recent_messages("aemeath", "g", 10)
        store.close()
        return first, second, row, messages, platform.calls

    first, second, row, messages, calls = asyncio.run(scenario())
    assert first.reason == "send_unknown"
    assert second.reason == "duplicate_outbox"
    assert row["status"] == "unknown"
    assert calls == 1
    assert messages == []


def test_expired_delivery_never_calls_platform(tmp_path):
    async def scenario():
        store = SQLiteMemoryStore(tmp_path / "expired.db")
        platform = ReceiptPlatform(SendResult.confirmed())
        service = DeliveryService(platform, store, FakeClock(201), persona_id="aemeath")
        outcome = await service.deliver(plan("expired"))
        row = store.outbox_record("aemeath", "expired")
        store.close()
        return outcome, row, platform.calls

    outcome, row, calls = asyncio.run(scenario())
    assert outcome.reason == "delivery_expired"
    assert row["status"] == "expired"
    assert calls == 0


def test_cancellation_before_send_marks_expired(tmp_path):
    async def scenario():
        store = SQLiteMemoryStore(tmp_path / "cancel-before.db")
        platform = ReceiptPlatform(SendResult.confirmed())
        service = DeliveryService(platform, store, FakeClock(101), persona_id="aemeath")
        delayed = DeliveryPlan(
            decision_id="cancel-before",
            group_id="g",
            segments=("稍后",),
            delay_seconds=60,
            expires_at=200,
        )
        task = asyncio.create_task(service.deliver(delayed))
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        row = store.outbox_record("aemeath", "cancel-before")
        store.close()
        return row

    assert asyncio.run(scenario())["status"] == "expired"


def test_cancellation_during_send_marks_unknown(tmp_path):
    class BlockingPlatform:
        def __init__(self):
            self.started = asyncio.Event()

        async def send_segments(self, *args, **kwargs):
            del args, kwargs
            self.started.set()
            await asyncio.Event().wait()

    async def scenario():
        store = SQLiteMemoryStore(tmp_path / "cancel-send.db")
        platform = BlockingPlatform()
        service = DeliveryService(platform, store, FakeClock(101), persona_id="aemeath")
        task = asyncio.create_task(service.deliver(plan("cancel-send")))
        await platform.started.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        row = store.outbox_record("aemeath", "cancel-send")
        store.close()
        return row

    assert asyncio.run(scenario())["status"] == "unknown"


def test_definite_platform_error_marks_failed(tmp_path):
    class FailedPlatform:
        async def send_segments(self, *args, **kwargs):
            del args, kwargs
            raise ValueError("rejected")

    async def scenario():
        store = SQLiteMemoryStore(tmp_path / "failed.db")
        service = DeliveryService(FailedPlatform(), store, FakeClock(101), persona_id="aemeath")
        outcome = await service.deliver(plan("failed"))
        row = store.outbox_record("aemeath", "failed")
        store.close()
        return outcome, row

    outcome, row = asyncio.run(scenario())
    assert outcome.reason == "send_error:ValueError"
    assert row["status"] == "failed"


def test_platform_timeout_marks_unknown(tmp_path):
    class TimeoutPlatform:
        async def send_segments(self, *args, **kwargs):
            del args, kwargs
            raise asyncio.TimeoutError()

    async def scenario():
        store = SQLiteMemoryStore(tmp_path / "timeout.db")
        service = DeliveryService(TimeoutPlatform(), store, FakeClock(101), persona_id="aemeath")
        outcome = await service.deliver(plan("timeout"))
        row = store.outbox_record("aemeath", "timeout")
        store.close()
        return outcome, row

    outcome, row = asyncio.run(scenario())
    assert outcome.reason == "send_unknown"
    assert row["status"] == "unknown"


def test_startup_recovers_orphaned_sending_as_unknown(tmp_path):
    async def create_orphan(path):
        store = SQLiteMemoryStore(path)
        assert store.enqueue_outbox("aemeath", "orphan", "g", "x", 1, 100)
        assert await store.transition_outbox_async(
            "aemeath", "orphan", "pending", "sending", increment_attempt=True
        )
        store.close()

    path = tmp_path / "recovery.db"
    asyncio.run(create_orphan(path))
    reopened = SQLiteMemoryStore(path)
    row = reopened.outbox_record("aemeath", "orphan")
    reopened.close()
    assert row["status"] == "unknown"
    assert row["failure_code"] == "startup_recovery"
