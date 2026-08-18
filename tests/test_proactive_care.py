import asyncio

from groupmate.memory.store import SQLiteMemoryStore
from groupmate.models import (
    ContinuityItem,
    ContinuityKind,
    ProactiveCareOutcome,
    RelationshipState,
    SendResult,
)
from groupmate.social.proactive_care import ProactiveCareScheduler


class CapturePlatform:
    def __init__(self):
        self.calls = []

    async def send_outbound(self, group_id, outbound, decision_id, quote_message_id=None):
        self.calls.append((group_id, tuple(outbound), decision_id, quote_message_id))
        return SendResult.confirmed()


def _item(summary="小明明天参加考试", quote="我明天考试"):
    return ContinuityItem(
        item_id="care-item",
        group_id="g1",
        subject_id="u1",
        kind=ContinuityKind.PLAN,
        summary=summary,
        source_message_id="m1",
        source_quote=quote,
        created_at=100,
        updated_at=100,
    )


def _scheduler(store, platform):
    return ProactiveCareScheduler(
        memory=store,
        platform_factory=lambda group_id: platform,
        persona_id="aemeath",
        character_name="爱弥斯",
        group_enabled=lambda group_id: True,
        paused_getter=lambda: False,
    )


def _set_relationship(store, *, familiarity=50, affinity=50, trust=30, boundary=0):
    store.upsert_profile("aemeath", "g1", "u1", "小明", "", 1, updated_at=100)
    store.upsert_relationship_state(
        "aemeath",
        RelationshipState(
            group_id="g1", user_id="u1", familiarity=familiarity,
            affinity=affinity, trust=trust, boundary_pressure=boundary, updated_at=100,
        ),
    )


def test_close_relationship_speaks_once_and_keeps_reason(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "care-close.db")
    platform = CapturePlatform()
    try:
        _set_relationship(store)
        store.append_continuity_item("aemeath", _item())
        first = asyncio.run(_scheduler(store, platform).run_due(now=100 + 2 * 86400))
        second = asyncio.run(_scheduler(store, platform).run_due(now=100 + 12 * 86400))
        decisions = store.list_proactive_care("aemeath", item_id="care-item")
        assert first["spoken"] == 1
        assert second["processed"] == 0
        assert decisions[0].outcome is ProactiveCareOutcome.SPOKE
        assert decisions[0].reason_code == "relationship_and_elapsed_time"
        assert decisions[0].sent_at is not None
        assert len(platform.calls) == 1
    finally:
        store.close()


def test_general_relationship_stays_silent(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "care-general.db")
    try:
        _set_relationship(store, familiarity=5, affinity=0, trust=0)
        store.append_continuity_item("aemeath", _item())
        result = asyncio.run(_scheduler(store, CapturePlatform()).run_due(now=200000))
        decision = store.list_proactive_care("aemeath")[0]
        assert result["silent"] == 1
        assert decision.outcome is ProactiveCareOutcome.SILENT
        assert decision.reason_code == "relationship_not_close"
    finally:
        store.close()


def test_sensitive_event_is_suppressed(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "care-sensitive.db")
    platform = CapturePlatform()
    try:
        _set_relationship(store)
        store.append_continuity_item("aemeath", _item("小明下周住院手术", "下周要住院"))
        asyncio.run(_scheduler(store, platform).run_due(now=200000))
        decision = store.list_proactive_care("aemeath")[0]
        assert decision.outcome is ProactiveCareOutcome.SUPPRESSED
        assert decision.reason_code == "sensitive_event"
        assert platform.calls == []
    finally:
        store.close()


def test_generic_open_loop_is_not_a_care_candidate(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "care-generic.db")
    platform = CapturePlatform()
    try:
        _set_relationship(store)
        store.append_continuity_item(
            "aemeath", _item("小明之后会发一张照片", "之后发给你看")
        )
        result = asyncio.run(_scheduler(store, platform).run_due(now=200000))
        assert result["processed"] == 0
        assert store.list_proactive_care("aemeath") == []
        assert platform.calls == []
    finally:
        store.close()


def test_admin_correction_permanently_stops_item(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "care-corrected.db")
    platform = CapturePlatform()
    try:
        _set_relationship(store, familiarity=5, affinity=0)
        store.append_continuity_item("aemeath", _item())
        scheduler = _scheduler(store, platform)
        asyncio.run(scheduler.run_due(now=200000))
        care = store.list_proactive_care("aemeath")[0]
        store.correct_proactive_care("aemeath", care.care_id, reason="不应主动提起", now=200100)
        _set_relationship(store, familiarity=90, affinity=80, trust=80)
        result = asyncio.run(scheduler.run_due(now=5000000))
        assert result["processed"] == 0
        assert platform.calls == []
    finally:
        store.close()
