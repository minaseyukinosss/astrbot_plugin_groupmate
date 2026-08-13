import asyncio

from groupmate.core.addressee import AddresseeResolver
from groupmate.models import (
    ContinuityItem,
    ContinuityKind,
    ContinuityStatus,
    TopicSnapshot,
    TriggerKind,
)
from groupmate.social.continuity import ContinuityWriter
from groupmate.memory.store import SQLiteMemoryStore


class ContinuityModel:
    def __init__(self, payload):
        self.payload = payload

    async def extract_continuity_update(self, **kwargs):
        del kwargs
        return self.payload


def direct_targeting(topic):
    return AddresseeResolver().resolve(topic, TriggerKind.ALIAS_DIRECT, aliases=("爱弥斯",))


def test_continuity_writer_opens_and_completes_source_grounded_item(tmp_path, message_factory):
    store = SQLiteMemoryStore(tmp_path / "continuity.db")
    try:
        open_topic = TopicSnapshot(
            "t1",
            "g",
            (message_factory(message_id="m1", sender_id="u1", sender_name="小明", text="我明天考完试告诉你结果", timestamp=100, mentions_bot=True),),
            100,
            100,
        )
        writer = ContinuityWriter(
            store,
            ContinuityModel(
                {
                    "action": "OPEN",
                    "kind": "follow_up",
                    "summary": "小明考完试后会告诉爱弥斯结果",
                    "evidence_quote": "我明天考完试告诉你结果",
                    "due_at": None,
                    "confidence": 0.96,
                }
            ),
            persona_id="aemeath",
        )
        opened = asyncio.run(
            writer.process(
                open_topic,
                direct_targeting(open_topic),
                decision_id="d1",
                now=101,
                reply_text="好，等你消息。",
            )
        )
        assert opened is not None
        assert opened.status is ContinuityStatus.OPEN

        complete_topic = TopicSnapshot(
            "t2",
            "g",
            (message_factory(message_id="m2", sender_id="u1", sender_name="小明", text="考完了，发挥得还行", timestamp=200, mentions_bot=True),),
            200,
            200,
        )
        writer.model = ContinuityModel(
            {
                "action": "COMPLETE",
                "item_id": opened.item_id,
                "kind": "follow_up",
                "summary": "",
                "evidence_quote": "考完了",
                "due_at": None,
                "confidence": 0.94,
            }
        )
        completed = asyncio.run(
            writer.process(
                complete_topic,
                direct_targeting(complete_topic),
                decision_id="d2",
                now=201,
                reply_text="那就好。",
            )
        )
        assert completed is not None
        assert completed.status is ContinuityStatus.COMPLETED
        assert completed.resolution_quote == "考完了"
    finally:
        store.close()


def test_continuity_writer_rejects_quote_not_in_latest_message(tmp_path, message_factory):
    store = SQLiteMemoryStore(tmp_path / "continuity-reject.db")
    try:
        topic = TopicSnapshot(
            "t1",
            "g",
            (message_factory(message_id="m1", sender_id="u1", text="我明天考试", timestamp=100, mentions_bot=True),),
            100,
            100,
        )
        writer = ContinuityWriter(
            store,
            ContinuityModel(
                {
                    "action": "OPEN",
                    "kind": "plan",
                    "summary": "对方明天考试",
                    "evidence_quote": "下周考试",
                    "confidence": 0.99,
                }
            ),
            persona_id="aemeath",
        )
        result = asyncio.run(
            writer.process(
                topic,
                direct_targeting(topic),
                decision_id="d1",
                now=101,
            )
        )
        assert result is None
        assert store.list_continuity_items("aemeath", group_id="g") == []
    finally:
        store.close()


def test_continuity_governance_status_is_revertible(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "continuity-governance.db")
    try:
        item = ContinuityItem(
            item_id="c1",
            group_id="g",
            subject_id="u1",
            kind=ContinuityKind.PLAN,
            summary="小明周五交作业",
            source_message_id="m1",
            source_quote="我周五交作业",
            created_at=100,
            updated_at=100,
        )
        store.append_continuity_item("aemeath", item)
        action = store.update_continuity_with_audit(
            "aemeath",
            "c1",
            status=ContinuityStatus.CANCELLED,
            reason="本人确认计划取消",
            actor="admin",
            now=200,
        )
        assert action["can_revert"] is True
        assert store.get_continuity_item("aemeath", "c1").status is ContinuityStatus.CANCELLED
        store.revert_governance_action(
            "aemeath",
            action["action_id"],
            reason="恢复误操作",
            actor="admin",
            now=300,
        )
        assert store.get_continuity_item("aemeath", "c1").status is ContinuityStatus.OPEN
    finally:
        store.close()


def test_continuity_lookup_expands_linked_member_identity(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "continuity-linked.db")
    try:
        store.upsert_profile("aemeath", "g", "old", "旧昵称", "", 1, updated_at=10)
        store.upsert_profile("aemeath", "g", "current", "小明", "", 1, updated_at=20)
        store.link_member_identity_with_audit(
            "aemeath",
            "g",
            "old",
            "current",
            reason="确认同一成员",
            actor="admin",
            now=30,
        )
        store.append_continuity_item(
            "aemeath",
            ContinuityItem(
                item_id="linked-item",
                group_id="g",
                subject_id="old",
                kind=ContinuityKind.FOLLOW_UP,
                summary="小明之后反馈结果",
                source_message_id="m1",
                source_quote="之后告诉你",
                created_at=15,
                updated_at=15,
            ),
        )
        subject_ids = store.member_subject_ids("aemeath", "g", "current")
        items = store.list_continuity_items(
            "aemeath",
            group_id="g",
            subject_ids=subject_ids,
            statuses=(ContinuityStatus.OPEN,),
        )
        assert [item.item_id for item in items] == ["linked-item"]
    finally:
        store.close()


def test_continuity_writer_is_idempotent_for_the_same_source_message(
    tmp_path, message_factory
):
    store = SQLiteMemoryStore(tmp_path / "continuity-idempotent.db")
    try:
        topic = TopicSnapshot(
            "t1",
            "g",
            (
                message_factory(
                    message_id="m1",
                    sender_id="u1",
                    text="我周五把照片发给你",
                    timestamp=100,
                    mentions_bot=True,
                ),
            ),
            100,
            100,
        )
        writer = ContinuityWriter(
            store,
            ContinuityModel(
                {
                    "action": "OPEN",
                    "kind": "promise",
                    "summary": "对方周五把照片发给爱弥斯",
                    "evidence_quote": "我周五把照片发给你",
                    "confidence": 0.97,
                }
            ),
            persona_id="aemeath",
        )
        targeting = direct_targeting(topic)
        first = asyncio.run(
            writer.process(topic, targeting, decision_id="d1", now=101)
        )
        second = asyncio.run(
            writer.process(topic, targeting, decision_id="d2", now=102)
        )
        assert first is not None
        assert second is None
        assert len(store.list_continuity_items("aemeath", group_id="g")) == 1
    finally:
        store.close()


def test_deleted_continuity_is_excluded_from_normal_status_queries(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "continuity-deleted.db")
    try:
        store.append_continuity_item(
            "aemeath",
            ContinuityItem(
                item_id="deleted-item",
                group_id="g",
                subject_id="u1",
                kind=ContinuityKind.PLAN,
                summary="已经不应继续展示的计划",
                source_message_id="m1",
                source_quote="这个计划忘掉吧",
                created_at=100,
                updated_at=100,
            ),
        )
        store.update_continuity_with_audit(
            "aemeath",
            "deleted-item",
            status=ContinuityStatus.DELETED,
            reason="本人要求遗忘",
            actor="admin",
            now=200,
        )
        visible = store.list_continuity_items(
            "aemeath",
            statuses=(
                ContinuityStatus.OPEN,
                ContinuityStatus.COMPLETED,
                ContinuityStatus.CANCELLED,
            ),
        )
        assert visible == []
        assert store.get_continuity_item(
            "aemeath", "deleted-item"
        ).status is ContinuityStatus.DELETED
    finally:
        store.close()
