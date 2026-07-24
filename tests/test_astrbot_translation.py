from groupmate.host import OneBotTranslator


def test_onebot_history_translation_preserves_reply_and_image():
    raw = {
        "message_id": "1",
        "group_id": "2",
        "user_id": "3",
        "time": 10,
        "sender": {"nickname": "Alice"},
        "message": [
            {"type": "reply", "data": {"id": "0"}},
            {"type": "text", "data": {"text": "看看这个"}},
            {"type": "image", "data": {"url": "https://example/image.jpg"}},
        ],
    }

    message = OneBotTranslator.from_history(raw, bot_id="9")

    assert message.text == "看看这个"
    assert message.reply_to_message_id == "0"
    assert message.image_urls == ("https://example/image.jpg",)
    assert message.segment_types == ("reply", "text", "image")


def test_onebot_translation_detects_bot_mention_and_self_message():
    mention = {
        "message_id": "2",
        "group_id": "2",
        "user_id": "3",
        "time": 10,
        "sender": {"nickname": "Alice"},
        "message": [{"type": "at", "data": {"qq": "9", "name": "Bot"}}],
    }
    self_message = dict(mention, message_id="3", user_id="9")

    assert OneBotTranslator.from_history(mention, bot_id="9").mentions_bot is True
    assert OneBotTranslator.from_history(self_message, bot_id="9").is_bot is True


def test_onebot_translation_coerces_missing_timestamp():
    raw = {
        "message_id": "4",
        "group_id": "2",
        "user_id": "3",
        "sender": {"nickname": "Alice"},
        "message": [{"type": "text", "data": {"text": "小爱"}}],
    }

    message = OneBotTranslator.from_history(raw, bot_id="9")

    assert message.timestamp > 0
