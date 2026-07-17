from groupmate.memory import SQLiteMemoryStore
from groupmate.models import MemoryItem, MemoryKind


def test_messages_are_idempotent_and_recent_ordered(tmp_path, message_factory):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    older = message_factory(message_id="older", timestamp=1, text="旧消息")
    newer = message_factory(message_id="newer", timestamp=2, text="新消息")

    assert store.save_message(newer) is True
    assert store.save_message(older) is True
    assert store.save_message(newer) is False

    assert store.recent_messages("g1", 10) == [older, newer]
    store.close()


def test_memory_retrieval_respects_expiry(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.add_memory(
        MemoryItem(
            memory_id="m1",
            group_id="g",
            subject_id="u",
            kind=MemoryKind.EPISODIC,
            text="Alice is preparing an exam",
            created_at=10,
            expires_at=20,
            confidence=0.9,
            importance=0.8,
            authority=1,
        )
    )

    assert store.search_memories("g", "Alice exam", now=15, limit=5)
    assert store.search_memories("g", "Alice exam", now=21, limit=5) == []
    store.close()


def test_profile_update_does_not_overwrite_higher_authority(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")

    assert store.upsert_profile("g", "u", "Alice", "闺蜜", authority=10) is True
    assert store.upsert_profile("g", "u", "New name", "普通", authority=1) is False

    profile = store.get_profile("g", "u")
    assert profile["display_name"] == "Alice"
    assert profile["relationship"] == "闺蜜"
    store.close()


def test_outbox_is_idempotent_by_decision_id(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")

    assert store.enqueue_outbox("d1", "g", "你好", created_at=10) is True
    assert store.enqueue_outbox("d1", "g", "重复", created_at=11) is False
    assert store.pending_outbox(now=12)[0]["text"] == "你好"
    store.mark_outbox_sent("d1", sent_at=13)
    assert store.pending_outbox(now=14) == []
    store.close()

