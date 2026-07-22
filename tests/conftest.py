import pytest

from groupmate.models import ChatMessage, GroupPolicy, TopicSnapshot


@pytest.fixture
def message_factory():
    def factory(**overrides):
        values = {
            "message_id": "m1",
            "group_id": "g1",
            "sender_id": "u1",
            "sender_name": "Alice",
            "text": "普通消息",
            "timestamp": 100,
        }
        values.update(overrides)
        return ChatMessage(**values)

    return factory


@pytest.fixture
def balanced_policy():
    return GroupPolicy(
        aliases=("爱弥斯", "小爱", "飞行雪绒"),
        debounce_min_seconds=0.01,
        debounce_max_seconds=0.01,
        spontaneous_cooldown_seconds=0,
        humanize_delay_enabled=False,
    )


@pytest.fixture
def topic_snapshot(message_factory):
    messages = (
        message_factory(message_id="m1", sender_name="Alice", text="今天也太热了"),
        message_factory(
            message_id="m2",
            sender_id="u2",
            sender_name="Bob",
            text="确实，空调都顶不住",
            timestamp=101,
        ),
    )
    return TopicSnapshot(
        topic_id="t1",
        group_id="g1",
        messages=messages,
        created_at=100,
        updated_at=101,
    )
