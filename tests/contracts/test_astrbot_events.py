from __future__ import annotations

from groupmate.adapters.astrbot_events import AstrBotEventTranslator
from groupmate.social_runtime.ownership import ExternalTriggerPolicy


def test_translator_preserves_platform_facts_without_social_inference():
    raw = {
        "message_id": "51",
        "self_id": "323537051",
        "group_id": "885617919",
        "user_id": "42",
        "time": 1700000000,
        "sender": {"nickname": "小夏", "card": "夏夏"},
        "message": [
            {"type": "reply", "data": {"id": "50"}},
            {"type": "at", "data": {"qq": "323537051", "name": "小爱"}},
            {"type": "text", "data": {"text": "看看这个"}},
            {"type": "image", "data": {"url": "https://example/image.jpg"}},
        ],
    }

    event = AstrBotEventTranslator("aemeath").translate(raw)

    assert event.event_id == "qq:51"
    assert event.source_message_id == "51"
    assert event.group_id == "885617919"
    assert event.actor_id == "42"
    assert event.occurred_at == 1700000000
    assert event.payload["text"] == "看看这个"
    assert event.payload["reply_to"] == "50"
    assert event.payload["mentions"] == ["323537051"]
    assert event.payload["mentions_bot"] is True
    assert event.payload["media"] == [
        {"type": "image", "url": "https://example/image.jpg"}
    ]
    assert event.payload["sender"] == {"id": "42", "name": "夏夏"}
    assert "should_reply" not in event.payload
    assert "scene_kind" not in event.payload
    assert "memory" not in event.payload


def test_missing_platform_id_uses_deterministic_segment_fingerprint():
    raw = {
        "group_id": "885617919",
        "user_id": "42",
        "time": 1700000000,
        "message": [
            {"data": {"text": "同一条消息"}, "type": "text"},
            {"data": {"qq": "7"}, "type": "at"},
        ],
    }
    translator = AstrBotEventTranslator("aemeath")

    first = translator.translate(raw)
    second = translator.translate(dict(raw))

    assert first.event_id == second.event_id
    assert first.event_id.startswith("qq:fingerprint:")
    assert first.source_message_id == first.event_id.removeprefix("qq:")


def test_translator_reads_astrbot_event_accessors_and_raw_message():
    class MessageObject:
        raw_message = {
            "message_id": "7",
            "time": 10,
            "message": [{"type": "text", "data": {"text": "早"}}],
        }

    class Event:
        message_obj = MessageObject()
        message_str = "早"

        @staticmethod
        def get_group_id():
            return "885617919"

        @staticmethod
        def get_sender_id():
            return "42"

        @staticmethod
        def get_sender_name():
            return "小夏"

    translated = AstrBotEventTranslator("aemeath").translate(Event())

    assert translated.group_id == "885617919"
    assert translated.actor_id == "42"
    assert translated.payload["sender"]["name"] == "小夏"


def test_translator_enriches_raw_at_and_reply_from_astrbot_components():
    class AtComponent:
        qq = "123"
        name = "仲手天尊"

        @staticmethod
        def toDict():
            return {"type": "at", "data": {"qq": "123"}}

    class ReplyComponent:
        id = "50"
        sender_id = "123"
        sender_nickname = "仲手天尊"
        time = 9
        message_str = "我再也不相信傻呗了"

        @staticmethod
        def toDict():
            return {"type": "reply", "data": {"id": "50"}}

    class PlainComponent:
        text = " 昨"

        @staticmethod
        def toDict():
            return {"type": "text", "data": {"text": " 昨"}}

    class MessageObject:
        self_id = "bot-1"
        raw_message = {
            "message_id": "51",
            "group_id": "885617919",
            "user_id": "42",
            "time": 10,
            "sender": {"card": "连金怪"},
            "message": [
                {"type": "reply", "data": {"id": "50"}},
                {"type": "at", "data": {"qq": "123"}},
                {"type": "text", "data": {"text": " 昨"}},
            ],
        }
        message = [ReplyComponent(), AtComponent(), PlainComponent()]

    class Event:
        message_obj = MessageObject()
        message_str = "昨"

    translated = AstrBotEventTranslator("aemeath").translate(Event())

    assert translated.payload["segments"][0]["data"] == {
        "id": "50",
        "sender_id": "123",
        "sender_nickname": "仲手天尊",
        "time": 9,
        "message_str": "我再也不相信傻呗了",
    }
    assert translated.payload["segments"][1]["data"] == {
        "qq": "123",
        "name": "仲手天尊",
    }
    assert translated.payload["reply_to_actor_id"] == "123"
    assert translated.payload["reply_to_bot"] is False


def test_translator_preserves_astrbot_route_and_reply_defaults():
    class MessageObject:
        self_id = "bot-1"
        raw_message = {
            "message_id": "52",
            "group_id": "885617919",
            "user_id": "42",
            "time": 10,
            "message": [
                {"type": "reply", "data": {"id": "51"}},
                {"type": "text", "data": {"text": "接着说"}},
            ],
        }

    class Event:
        message_obj = MessageObject()
        unified_msg_origin = "aiocqhttp:GroupMessage:885617919"

        @staticmethod
        def get_platform_id():
            return "onebot-main"

    translated = AstrBotEventTranslator("aemeath").translate(Event())

    assert translated.payload["bot_id"] == "bot-1"
    assert translated.payload["platform_id"] == "onebot-main"
    assert translated.payload["session"] == "aiocqhttp:GroupMessage:885617919"
    assert translated.payload["reply_to_actor_id"] is None
    assert translated.payload["reply_to_bot"] is False


def test_translator_derives_bot_identity_from_astrbot_message_object():
    class MessageObject:
        self_id = "bot-native-id"
        raw_message = {
            "message_id": "native-self",
            "group_id": "group-1",
            "user_id": "bot-native-id",
            "time": 10,
            "message": [
                {"type": "at", "data": {"qq": "bot-native-id"}},
                {"type": "text", "data": {"text": "状态"}},
            ],
        }

    class Event:
        message_obj = MessageObject()
        message_str = "状态"

    translated = AstrBotEventTranslator("persona:groupmate").translate(Event())

    assert translated.payload["is_self"] is True
    assert translated.payload["mentions_bot"] is True


def test_translator_preserves_optional_sender_and_automation_facts():
    translated = AstrBotEventTranslator("aemeath").translate(
        {
            "message_id": "source-facts",
            "self_id": "bot-1",
            "group_id": "group-1",
            "user_id": "42",
            "time": 10,
            "sender": {"nickname": "小夏", "role": "admin"},
            "automation_hint": "known_bot",
            "message": [{"type": "text", "data": {"text": "测试"}}],
        }
    )

    assert translated.payload["sender_role"] == "admin"
    assert translated.payload["automation_hint"] == "known_bot"


def test_translator_marks_only_configured_deployment_triggers_as_external():
    policy = ExternalTriggerPolicy.create(
        command_prefixes={"xw": "astrbot.waves"},
        link_domains={"v.douyin.com": "astrbot.video_parser"},
    )
    translator = AstrBotEventTranslator(
        "aemeath", external_trigger_policy=policy
    )

    command = translator.translate(
        {
            "message_id": "command",
            "group_id": "885617919",
            "user_id": "42",
            "time": 10,
            "message": [{"type": "text", "data": {"text": " xw帮助"}}],
        }
    )
    ordinary = translator.translate(
        {
            "message_id": "ordinary",
            "group_id": "885617919",
            "user_id": "42",
            "time": 11,
            "message": [
                {"type": "text", "data": {"text": "xwindow 怎么配置"}}
            ],
        }
    )

    assert command.payload["interaction_owner"] == "EXTERNAL_PLUGIN"
    assert command.payload["social_eligible"] is False
    assert command.payload["owner_ref"] == "astrbot.waves"
    assert command.payload["ownership_source"] == "configured_trigger"
    assert ordinary.payload["interaction_owner"] == "UNKNOWN"
    assert ordinary.payload["social_eligible"] is True


def test_translator_keeps_image_file_path_and_url():
    event = AstrBotEventTranslator("aemeath").translate(
        {
            "message_id": "52",
            "self_id": "323537051",
            "group_id": "885617919",
            "user_id": "42",
            "time": 1700000000,
            "message": [
                {
                    "type": "image",
                    "data": {
                        "file": "custom.gif",
                        "path": "/tmp/custom.gif",
                        "url": "http://127.0.0.1:3000/custom.gif",
                    },
                }
            ],
        }
    )

    assert event.payload["media"] == [
        {
            "type": "image",
            "url": "http://127.0.0.1:3000/custom.gif",
            "file": "custom.gif",
            "path": "/tmp/custom.gif",
        }
    ]
    assert event.payload["segments"][0]["data"]["url"] == "http://127.0.0.1:3000/custom.gif"
