import asyncio
import time
import types

from datetime import datetime
from zoneinfo import ZoneInfo

from groupmate.memory.store import SQLiteMemoryStore
from groupmate.models import (
    ContinuityItem,
    ContinuityKind,
    ContinuityStatus,
    OutboundKind,
    SelfCommitment,
    SelfCommitmentStatus,
    SendResult,
)
from groupmate.social.commitment_scheduler import CommitmentScheduler


class FakeCronManager:
    def __init__(self):
        self.added = []
        self.deleted = []

    async def add_basic_job(self, **kwargs):
        self.added.append(kwargs)
        return types.SimpleNamespace(job_id="cron-groupmate")

    async def list_jobs(self, job_type=None):
        del job_type
        return [types.SimpleNamespace(job_id="old-groupmate", name="Groupmate 承诺履约扫描")]

    async def delete_job(self, job_id):
        self.deleted.append(job_id)


class CapturePlatform:
    def __init__(self, *, succeeds=True):
        self.succeeds = succeeds
        self.calls = []

    async def send_outbound(
        self, group_id, outbound, decision_id, quote_message_id=None
    ):
        self.calls.append((group_id, tuple(outbound), decision_id, quote_message_id))
        if self.succeeds:
            return SendResult.confirmed()
        return SendResult.failed("platform_failed")


def _item(now, **overrides):
    values = {
        "commitment_id": "c1",
        "group_id": "g1",
        "beneficiary_subject_id": "10001",
        "summary": "提醒周五交材料",
        "source_decision_id": "source-d1",
        "source_message_id": "bot-source-d1",
        "source_quote": "到时候我提醒你交材料",
        "created_at": now - 100,
        "updated_at": now - 100,
        "fulfillment_mode": "reminder",
        "due_at": now - 1,
        "next_attempt_at": now - 1,
    }
    values.update(overrides)
    return SelfCommitment(**values)


def _scheduler(store, platform, cron=None, *, paused=False):
    context = types.SimpleNamespace(cron_manager=cron)
    return CommitmentScheduler(
        context=context,
        memory=store,
        persona_id="aemeath",
        character_name="爱弥斯",
        platform_factory=lambda group_id: platform,
        capability_governor_factory=lambda group_id: None,
        paused_getter=lambda: paused,
        group_enabled=lambda group_id: True,
        provider_getter=lambda group_id: "provider",
    )


def test_scheduler_uses_astrbot_basic_cron_and_removes_it(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "scheduler-cron.db")
    cron = FakeCronManager()
    scheduler = _scheduler(store, CapturePlatform(), cron)
    try:
        assert asyncio.run(scheduler.start()) == "astrbot_cron"
        assert cron.added[0]["persistent"] is False
        assert cron.added[0]["cron_expression"] == "* * * * *"
        assert cron.added[0]["name"] == "爱弥斯到点提醒扫描"
        assert scheduler._fallback_task is not None
        assert asyncio.run(scheduler.start()) == "astrbot_cron"
        assert len(cron.added) == 1
        asyncio.run(scheduler.close())
        assert cron.deleted == ["old-groupmate", "cron-groupmate"]
    finally:
        store.close()


def test_due_reminder_mentions_member_and_completes_once(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "scheduler-reminder.db")
    platform = CapturePlatform()
    scheduler = _scheduler(store, platform)
    now = int(time.time())
    try:
        store.append_self_commitment("aemeath", _item(now))
        first = asyncio.run(
            scheduler.run_due(commitment_id="c1", force=True)
        )
        second = asyncio.run(
            scheduler.run_due(commitment_id="c1", force=True)
        )
        updated = store.get_self_commitment("aemeath", "c1")
        assert first["processed"] == 1
        assert second["processed"] == 0
        assert updated.status is SelfCommitmentStatus.COMPLETED
        assert updated.attempt_count == 1
        assert updated.last_delivery_at is not None
        outbound = platform.calls[0][1]
        assert outbound[0].kind is OutboundKind.MENTION
        assert outbound[0].target_user_id == "10001"
        assert "交材料" in outbound[1].text
    finally:
        store.close()


def test_failed_delivery_stays_pending_for_retry(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "scheduler-retry.db")
    scheduler = _scheduler(store, CapturePlatform(succeeds=False))
    now = int(time.time())
    try:
        store.append_self_commitment("aemeath", _item(now))
        asyncio.run(scheduler.run_due(commitment_id="c1", force=True))
        updated = store.get_self_commitment("aemeath", "c1")
        assert updated.status is SelfCommitmentStatus.PENDING
        assert updated.next_attempt_at is not None
        assert updated.last_delivery_at is None
        assert updated.failure_code == "send_failed"
    finally:
        store.close()


def test_follow_up_due_without_new_facts_is_blocked_without_message(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "scheduler-follow-up.db")
    platform = CapturePlatform()
    scheduler = _scheduler(store, platform)
    now = int(time.time())
    try:
        store.append_self_commitment(
            "aemeath",
            _item(
                now,
                summary="有审核结果后告诉小明",
                fulfillment_mode="follow_up",
            ),
        )
        asyncio.run(scheduler.run_due(commitment_id="c1", force=True))
        updated = store.get_self_commitment("aemeath", "c1")
        assert updated.status is SelfCommitmentStatus.BLOCKED
        assert updated.failure_code == "waiting_for_new_information"
        assert platform.calls == []
    finally:
        store.close()


def test_paused_scheduler_never_claims_or_sends_due_commitment(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "scheduler-paused.db")
    platform = CapturePlatform()
    scheduler = _scheduler(store, platform, paused=True)
    now = int(time.time())
    try:
        store.append_self_commitment("aemeath", _item(now))
        result = asyncio.run(scheduler.run_due(commitment_id="c1", force=True))
        updated = store.get_self_commitment("aemeath", "c1")
        assert result["reason"] == "paused"
        assert updated.status is SelfCommitmentStatus.PENDING
        assert updated.attempt_count == 0
        assert platform.calls == []
    finally:
        store.close()


def _shanghai_night():
    return int(datetime(2026, 8, 13, 1, 30, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())


def test_short_reminder_fires_during_quiet_hours(tmp_path, monkeypatch):
    night = _shanghai_night()
    monkeypatch.setattr(
        "groupmate.social.commitment_scheduler.time.time", lambda: night
    )
    store = SQLiteMemoryStore(tmp_path / "scheduler-quiet-short.db")
    platform = CapturePlatform()
    scheduler = _scheduler(store, platform)
    try:
        store.append_self_commitment(
            "aemeath",
            _item(
                night,
                created_at=night - 60,
                updated_at=night - 60,
                due_at=night - 1,
                next_attempt_at=night - 1,
            ),
        )
        result = asyncio.run(scheduler.run_due())
        updated = store.get_self_commitment("aemeath", "c1")
        assert result["reason"] == "ok"
        assert updated.status is SelfCommitmentStatus.COMPLETED
        assert platform.calls
        assert "交材料" in platform.calls[0][1][1].text
        assert "提醒交材料" not in platform.calls[0][1][1].text
    finally:
        store.close()


def test_long_reminder_defers_during_quiet_hours(tmp_path, monkeypatch):
    night = _shanghai_night()
    monkeypatch.setattr(
        "groupmate.social.commitment_scheduler.time.time", lambda: night
    )
    store = SQLiteMemoryStore(tmp_path / "scheduler-quiet-long.db")
    platform = CapturePlatform()
    scheduler = _scheduler(store, platform)
    try:
        store.append_self_commitment(
            "aemeath",
            _item(
                night,
                created_at=night - 3 * 3600,
                updated_at=night - 3 * 3600,
                due_at=night - 1,
                next_attempt_at=night - 1,
            ),
        )
        result = asyncio.run(scheduler.run_due())
        updated = store.get_self_commitment("aemeath", "c1")
        assert result["reason"] == "quiet_hours"
        assert updated.status is SelfCommitmentStatus.PENDING
        assert updated.failure_code == "quiet_hours_deferred"
        assert platform.calls == []
    finally:
        store.close()


def test_short_reminder_ignores_group_busy(tmp_path, message_factory):
    now = int(time.time())
    store = SQLiteMemoryStore(tmp_path / "scheduler-busy-short.db")
    platform = CapturePlatform()
    scheduler = _scheduler(store, platform)
    try:
        for index in range(4):
            store.save_message(
                "aemeath",
                message_factory(
                    message_id="busy-{}".format(index),
                    group_id="g1",
                    sender_id="u{}".format(index),
                    sender_name="成员{}".format(index),
                    text="在聊{}".format(index),
                    timestamp=now - 10,
                    is_bot=False,
                ),
            )
        store.append_self_commitment(
            "aemeath",
            _item(
                now,
                created_at=now - 60,
                updated_at=now - 60,
                due_at=now - 1,
                next_attempt_at=now - 1,
            ),
        )
        asyncio.run(scheduler.run_due())
        updated = store.get_self_commitment("aemeath", "c1")
        assert updated.status is SelfCommitmentStatus.COMPLETED
        assert platform.calls
    finally:
        store.close()


def test_due_reminder_is_withdrawn_if_sender_cancelled_in_chat(
    tmp_path, message_factory
):
    store = SQLiteMemoryStore(tmp_path / "scheduler-cancel-chat.db")
    platform = CapturePlatform()
    scheduler = _scheduler(store, platform)
    now = int(time.time())
    try:
        store.append_self_commitment(
            "aemeath",
            _item(
                now,
                created_at=now - 80,
                updated_at=now - 80,
                due_at=now - 1,
                next_attempt_at=now - 1,
            ),
        )
        store.save_message(
            "aemeath",
            message_factory(
                message_id="cancel-1",
                group_id="g1",
                sender_id="10001",
                sender_name="复读斥候",
                text="算了，不用提醒我了",
                timestamp=now - 40,
                is_bot=False,
            ),
        )
        asyncio.run(scheduler.run_due(commitment_id="c1", force=True))
        updated = store.get_self_commitment("aemeath", "c1")
        assert updated.status is SelfCommitmentStatus.WITHDRAWN
        assert updated.failure_code == "cancelled_before_due"
        assert platform.calls == []
    finally:
        store.close()


def test_next_wake_delay_sleeps_until_due(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "scheduler-wake.db")
    scheduler = _scheduler(store, CapturePlatform())
    now = 1_700_000_000
    try:
        assert scheduler._next_wake_delay(now) == 15.0
        store.append_self_commitment(
            "aemeath",
            _item(now, due_at=now + 3, next_attempt_at=now + 3),
        )
        assert scheduler._next_wake_delay(now) == 3.0
    finally:
        store.close()


def test_delivered_reminder_closes_matching_continuity_item(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "scheduler-continuity.db")
    platform = CapturePlatform()
    scheduler = _scheduler(store, platform)
    now = int(time.time())
    try:
        store.append_self_commitment(
            "aemeath",
            _item(now, request_message_id="user-m1"),
        )
        store.append_continuity_item(
            "aemeath",
            ContinuityItem(
                item_id="cont-1",
                group_id="g1",
                subject_id="10001",
                kind=ContinuityKind.PLAN,
                summary="复读斥候要求小爱在1分钟后提醒自己交材料",
                source_message_id="user-m1",
                source_quote="小爱，1分钟后提醒我交材料",
                created_at=now - 100,
                updated_at=now - 100,
            ),
        )
        asyncio.run(scheduler.run_due(commitment_id="c1", force=True))
        assert store.get_self_commitment(
            "aemeath", "c1"
        ).status is SelfCommitmentStatus.COMPLETED
        assert store.get_continuity_item(
            "aemeath", "cont-1"
        ).status is ContinuityStatus.COMPLETED
    finally:
        store.close()
