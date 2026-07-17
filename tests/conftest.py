import pytest

from groupmate.models import ChatMessage, GroupPolicy


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
    )

