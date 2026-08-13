import asyncio

from groupmate.host import NapCatHistoryPort, OneBotTranslator


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


def test_onebot_translation_keeps_at_id_when_display_name_missing():
    raw = {
        "message_id": "5",
        "group_id": "2",
        "user_id": "3",
        "time": 10,
        "sender": {"nickname": "Alice"},
        "message": [
            {"type": "text", "data": {"text": "小爱把她"}},
            {"type": "at", "data": {"qq": "3229586160"}},
            {"type": "text", "data": {"text": "禁言十分钟"}},
        ],
    }

    message = OneBotTranslator.from_history(raw, bot_id="9")

    assert message.mentioned_user_ids == ("3229586160",)
    assert "@某人" in message.text
    assert "3229586160" not in message.text
    assert "禁言十分钟" in message.text
    assert message.metadata["anonymous_mention_ids"] == ["3229586160"]


def test_onebot_translation_keeps_at_id_and_human_display_name_together():
    raw = {
        "message_id": "6",
        "group_id": "2",
        "user_id": "3",
        "time": 10,
        "sender": {"nickname": "Alice"},
        "message": [
            {"type": "text", "data": {"text": "找"}},
            {"type": "at", "data": {"qq": "42", "name": "小夏"}},
        ],
    }

    message = OneBotTranslator.from_history(raw, bot_id="9")

    assert message.text == "找@小夏"
    assert message.mentioned_user_ids == ("42",)
    assert message.metadata["mention_names"] == {"42": "小夏"}


def test_history_port_resolves_anonymous_at_to_group_card():
    class Bot:
        def __init__(self):
            self.calls = []

        async def call_action(self, action, **kwargs):
            self.calls.append((action, kwargs))
            if action == "get_group_msg_history":
                return {
                    "messages": [
                        {
                            "message_id": "7",
                            "group_id": "2",
                            "user_id": "3",
                            "time": 10,
                            "sender": {"nickname": "Alice"},
                            "message": [
                                {"type": "at", "data": {"qq": "42"}}
                            ],
                        }
                    ]
                }
            if action == "get_group_member_info":
                assert kwargs["group_id"] == 2
                assert kwargs["user_id"] == 42
                return {"card": "小夏", "nickname": "Summer"}
            raise AssertionError(action)

    bot = Bot()
    message = asyncio.run(NapCatHistoryPort(bot, "9").fetch_recent("2", 20))[0]

    assert message.text == "@小夏"
    assert message.mentioned_user_ids == ("42",)
    assert message.metadata["mention_names"] == {"42": "小夏"}
    assert message.metadata["anonymous_mention_ids"] == []


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


def test_event_translation_uses_resolved_reply_sender_to_detect_bot():
    class ReplyComponent:
        type = "Reply"
        id = "50"
        sender_id = "9"

    class MessageObject:
        raw_message = {
            "message_id": "51",
            "group_id": "2",
            "user_id": "3",
            "time": 10,
            "sender": {"nickname": "Alice"},
            "message": [
                {"type": "reply", "data": {"id": "50"}},
                {"type": "text", "data": {"text": "那这个呢"}},
            ],
        }
        message = [ReplyComponent()]

    class Event:
        message_obj = MessageObject()
        is_at_or_wake_command = True
        message_str = "那这个呢"

        @staticmethod
        def get_group_id():
            return "2"

        @staticmethod
        def get_sender_id():
            return "3"

        @staticmethod
        def get_sender_name():
            return "Alice"

    message = OneBotTranslator.from_event(Event(), bot_id="9")

    assert message.reply_to_message_id == "50"
    assert message.reply_to_bot is True
