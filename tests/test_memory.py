import asyncio

from groupmate.memory import SQLiteMemoryStore
from groupmate.models import MemoryItem, MemoryKind
from groupmate.models import ChatMessage, SocialEvent, SocialEventKind


def test_messages_are_idempotent_and_recent_ordered(tmp_path, message_factory):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    older = message_factory(message_id="older", timestamp=1, text="旧消息")
    newer = message_factory(message_id="newer", timestamp=2, text="新消息")

    assert store.save_message("aemeath", newer) is True
    assert store.save_message("aemeath", older) is True
    assert store.save_message("aemeath", newer) is False

    assert store.recent_messages("aemeath", "g1", 10) == [older, newer]
    store.close()


def test_memory_retrieval_respects_expiry(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.add_memory("aemeath",
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

    assert store.search_memories("aemeath", "g", "Alice exam", now=15, limit=5)
    assert store.search_memories("aemeath", "g", "Alice exam", now=21, limit=5) == []
    store.close()


def test_profile_update_does_not_overwrite_higher_authority(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")

    assert store.upsert_profile("aemeath", "g", "u", "Alice", "闺蜜", authority=10) is True
    assert store.upsert_profile("aemeath", "g", "u", "New name", "普通", authority=1) is False

    profile = store.get_profile("aemeath", "g", "u")
    assert profile["display_name"] == "Alice"
    assert profile["relationship"] == "闺蜜"
    store.close()


def test_message_observation_tracks_member_and_nickname_history(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    first = ChatMessage(
        message_id="m1", group_id="g", sender_id="u1", sender_name="旧名",
        text="你好", timestamp=10, mentioned_user_ids=("u2",),
        metadata={"mention_names": {"u2": "被点名的人"}},
    )
    renamed = ChatMessage(
        message_id="m2", group_id="g", sender_id="u1", sender_name="新名",
        text="又见面", timestamp=20,
    )

    assert store.save_message("aemeath", first)
    assert store.save_message("aemeath", renamed)

    profiles = store.list_member_profiles("aemeath", group_id="g")
    sender = next(item for item in profiles if item["subject_id"] == "u1")
    mentioned = next(item for item in profiles if item["subject_id"] == "u2")
    assert sender["display_name"] == "新名"
    assert [item["name"] for item in sender["nickname_history"]] == ["旧名", "新名"]
    assert sender["first_seen_at"] == 10
    assert sender["last_seen_at"] == 20
    assert mentioned["display_name"] == "被点名的人"
    store.close()


def test_same_nickname_keeps_distinct_member_profiles(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    for subject_id, message_id in (("u1", "m1"), ("u2", "m2")):
        store.save_message(
            "aemeath",
            ChatMessage(
                message_id=message_id, group_id="g", sender_id=subject_id,
                sender_name="同名", text="在", timestamp=10,
            ),
        )
    profiles = store.list_member_profiles("aemeath", group_id="g")
    assert {item["subject_id"] for item in profiles} == {"u1", "u2"}
    store.close()


def test_member_name_index_uses_history_and_rejects_duplicate_names(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.save_message(
        "aemeath",
        ChatMessage("m1", "g", "u1", "旧昵称", "在", 10),
    )
    store.save_message(
        "aemeath",
        ChatMessage("m2", "g", "u1", "新昵称", "在", 20),
    )
    store.correct_member_profile_with_audit(
        "aemeath", "g", "u1", "小明", reason="本人确认", actor="admin", now=21
    )
    index = store.member_name_index("aemeath", "g")
    assert index["旧昵称"] == "u1"
    assert index["新昵称"] == "u1"
    assert index["小明"] == "u1"

    store.save_message(
        "aemeath",
        ChatMessage("m3", "g", "u2", "新昵称", "在", 30),
    )
    assert store.member_name_index("aemeath", "g")["新昵称"] == ""
    store.close()


def test_linked_member_memory_and_relationship_read_as_one_person(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    for subject_id, name in (("old", "旧档案"), ("current", "当前档案")):
        store.save_message(
            "aemeath",
            ChatMessage(subject_id, "g", subject_id, name, "在", 10),
        )
    store.add_memory(
        "aemeath",
        MemoryItem(
            memory_id="memory-old", group_id="g", subject_id="old",
            kind=MemoryKind.PROFILE, text="喜欢黑咖啡", created_at=11,
        ),
    )
    store.record_social_interaction(
        "aemeath",
        SocialEvent(
            event_id="old-thanks", group_id="g", user_id="old",
            kind=SocialEventKind.THANKS, source_message_id="m-old",
            confidence=1.0, occurred_at=12,
        ),
    )
    store.link_member_identity_with_audit(
        "aemeath", "g", "old", "current",
        reason="确认同一人", actor="admin", now=20,
    )

    memories = store.search_memories(
        "aemeath", "g", "黑咖啡", now=30, limit=5,
        subject_ids=("current",),
    )
    relationship = store.get_member_relationship_state(
        "aemeath", "g", "current", now=30
    )
    assert [item.memory_id for item in memories] == ["memory-old"]
    assert relationship is not None
    assert relationship.user_id == "current"
    assert relationship.affinity == 2
    assert relationship.interaction_count == 1
    store.close()


def test_member_correction_and_identity_link_are_audited_and_revertible(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    for subject_id, name in (("u1", "旧档案"), ("u2", "正确成员")):
        store.save_message(
            "aemeath",
            ChatMessage(
                message_id=subject_id, group_id="g", sender_id=subject_id,
                sender_name=name, text="在", timestamp=10,
            ),
        )

    corrected = store.correct_member_profile_with_audit(
        "aemeath", "g", "u2", "小明", reason="本人确认", actor="admin", now=20
    )
    assert corrected["profile"]["address"] == "小明"
    store.revert_governance_action(
        "aemeath", corrected["action"]["action_id"], reason="撤销", actor="admin", now=21
    )
    assert store.get_profile("aemeath", "g", "u2")["preferred_address"] == ""

    link = store.link_member_identity_with_audit(
        "aemeath", "g", "u1", "u2", reason="确认同一人", actor="admin", now=30
    )
    assert store.resolve_member_subject_id("aemeath", "g", "u1") == "u2"
    store.record_social_interaction(
        "aemeath",
        SocialEvent(
            event_id="e1", group_id="g", user_id="u1",
            kind=SocialEventKind.THANKS, source_message_id="m3",
            confidence=1.0, occurred_at=31,
        ),
    )
    assert store.get_relationship_state("aemeath", "g", "u2") is not None
    assert store.get_relationship_state("aemeath", "g", "u1") is None
    store.revert_governance_action(
        "aemeath", link["action_id"], reason="关联有误", actor="admin", now=32
    )
    assert store.resolve_member_subject_id("aemeath", "g", "u1") == "u1"
    store.close()


def test_outbox_is_idempotent_by_decision_id(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")

    assert store.enqueue_outbox("aemeath", "d1", "g", "你好", created_at=10) is True
    assert store.enqueue_outbox("aemeath", "d1", "g", "重复", created_at=11) is False
    assert store.pending_outbox("aemeath", now=12)[0]["text"] == "你好"
    assert asyncio.run(
        store.transition_outbox_async("aemeath", "d1", "pending", "sent")
    )
    assert store.pending_outbox("aemeath", now=14) == []
    store.close()
