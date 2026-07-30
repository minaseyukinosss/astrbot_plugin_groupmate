"""Cross-persona isolation at the SQLite store boundary."""

from inspect import signature

import pytest

from groupmate.memory.store import SQLiteMemoryStore
from groupmate.models import SocialEvent, SocialEventKind


@pytest.fixture
def store(tmp_path):
    value = SQLiteMemoryStore(tmp_path / "persona-scope.db")
    try:
        yield value
    finally:
        value.close()


def test_same_platform_message_is_isolated_by_persona(store, message_factory):
    message = message_factory(message_id="same-message", group_id="g1")

    assert store.save_message("aemeath", message)
    assert store.save_message("future", message)
    assert [
        item.message_id for item in store.recent_messages("aemeath", "g1", 10)
    ] == [message.message_id]
    assert [
        item.message_id for item in store.recent_messages("future", "g1", 10)
    ] == [message.message_id]


def test_outbox_and_decisions_require_matching_persona(store):
    assert store.enqueue_outbox("aemeath", "d1", "g1", "在呢。", 100)
    assert store.outbox_record("aemeath", "d1") is not None
    assert store.outbox_record("future", "d1") is None

    store.record_transition("aemeath", "d1", "g1", "END", "sent", 101)
    assert store.recent_decision_ends("aemeath", "g1")[0]["decision_id"] == "d1"
    assert store.recent_decision_ends("future", "g1") == []


def test_profiles_are_isolated_by_persona(store):
    assert store.upsert_profile(
        "aemeath", "g1", "u1", "Alice", "闺蜜", authority=10
    )
    assert store.get_profile("future", "g1", "u1") is None


def test_empty_persona_id_is_rejected_before_sql(store, message_factory):
    with pytest.raises(ValueError, match="persona_id"):
        store.save_message("", message_factory())


def test_topic_epochs_are_isolated_by_persona(store):
    assert store.open_topic_epoch("aemeath", "g1", "topic-a", 100)
    assert store.open_topic_epoch("future", "g1", "topic-f", 101)

    assert store.latest_open_topic_epoch("aemeath", "g1")["topic_id"] == "topic-a"
    assert store.latest_open_topic_epoch("future", "g1")["topic_id"] == "topic-f"

    assert store.close_topic_epoch("aemeath", "g1", "topic-a", 110, "RESET")
    assert store.latest_open_topic_epoch("aemeath", "g1") is None
    assert store.latest_open_topic_epoch("future", "g1")["topic_id"] == "topic-f"


def test_continuation_grants_are_isolated_by_persona(store):
    assert store.grant_continuation(
        persona_id="aemeath",
        grant_id="grant-a",
        group_id="g1",
        sender_id="u1",
        opened_by_decision_id="decision-a",
        opened_by_message_id="message-a",
        trigger_kind="ALIAS_DIRECT",
        granted_at=100,
        expires_at=190,
        max_total_seconds=300,
    )

    assert store.latest_continuation_grant("aemeath", "g1", 120, "u1") is not None
    assert store.latest_continuation_grant("future", "g1", 120, "u1") is None
    assert store.list_active_continuation_grants("future", "g1", 120) == []


def test_relationship_state_is_isolated_by_persona(store):
    event = SocialEvent(
        event_id="event-a",
        group_id="g1",
        user_id="u1",
        kind=SocialEventKind.THANKS,
        source_message_id="message-a",
        confidence=1.0,
        occurred_at=100,
        decision_id="decision-a",
    )

    aemeath = store.record_social_interaction(
        "aemeath",
        event,
        configured_relationship="闺蜜",
        now=100,
    )

    assert aemeath.affinity >= 50
    assert store.get_relationship_state("future", "g1", "u1") is None


def test_state_store_public_contracts_require_explicit_persona_id():
    state_methods = (
        "save_message",
        "recent_messages",
        "upsert_profile",
        "get_profile",
        "add_memory",
        "search_memories",
        "append_memory_candidate",
        "purge_expired_memories",
        "record_transition",
        "enqueue_outbox",
        "enqueue_outbox_async",
        "outbox_record",
        "list_candidate_sent_at",
        "list_spontaneous_sent_at",
        "latest_open_topic_epoch",
        "open_topic_epoch",
        "close_topic_epoch",
        "grant_continuation",
        "grant_continuation_async",
        "latest_continuation_grant",
        "append_social_event",
        "get_relationship_state",
        "record_social_interaction",
    )

    for name in state_methods:
        assert "persona_id" in signature(
            getattr(SQLiteMemoryStore, name)
        ).parameters, name
