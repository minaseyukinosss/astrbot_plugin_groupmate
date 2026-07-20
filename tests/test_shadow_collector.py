import json

from groupmate.evaluation.collector import ShadowCollector
from groupmate.models import ChatMessage, TopicSnapshot


def topic(messages):
    return TopicSnapshot("t", "real-group-123", tuple(messages), 1, messages[-1].timestamp)


def message(index, sender_id="real-user-456", sender_name="真实昵称", **overrides):
    values = {
        "message_id": "m{}".format(index),
        "group_id": "real-group-123",
        "sender_id": sender_id,
        "sender_name": sender_name,
        "text": "消息{}".format(index),
        "timestamp": index,
        "metadata": {"raw": {"user_id": "real-user-456", "url": "https://secret"}},
    }
    values.update(overrides)
    return ChatMessage(**values)


def test_default_collection_stores_features_without_context():
    sample = ShadowCollector(store_text=False).collect(topic([message(1), message(2)]))
    assert sample.context is None
    assert sample.features["message_count"] == 2
    assert sample.features["participant_count"] == 1


def test_text_collection_uses_pseudonyms_and_drops_raw_metadata():
    messages = [
        message(1, text="看这个", image_urls=("https://secret/image.png",)),
        message(2, sender_id="real-user-789", sender_name="另一个真实昵称"),
    ]
    sample = ShadowCollector(store_text=True).collect(topic(messages))
    encoded = json.dumps(sample.context, ensure_ascii=False)
    assert "成员1" in encoded and "成员2" in encoded
    assert "real-user" not in encoded
    assert "真实昵称" not in encoded
    assert "https://" not in encoded
    assert "metadata" not in encoded
    assert "[图片]" in encoded


def test_collection_keeps_at_most_twenty_messages_and_five_minutes():
    messages = [message(index, timestamp=index * 20) for index in range(1, 31)]
    sample = ShadowCollector(store_text=True).collect(topic(messages))
    assert len(sample.context) == 16
    assert sample.context[0]["message_id"] == "m15"
