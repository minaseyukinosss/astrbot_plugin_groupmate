from groupmate.astrbot_adapter import OneBotTranslator, parse_decision_response
from groupmate.models import DecisionAction, TriggerKind, Urgency


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


def test_decision_parser_accepts_fenced_json_and_clamps_values():
    decision = parse_decision_response(
        """```json
        {"action":"respond","confidence":4,"reason_code":"helpful",
         "contribution":"给一句短反应","urgency":"high","needs_vision":true}
        ```""",
        trigger=TriggerKind.CANDIDATE,
    )

    assert decision.action is DecisionAction.RESPOND
    assert decision.confidence == 1.0
    assert decision.urgency is Urgency.HIGH
    assert decision.needs_vision is True


def test_decision_parser_fails_closed_on_invalid_json():
    decision = parse_decision_response("not json", trigger=TriggerKind.CANDIDATE)

    assert decision.action is DecisionAction.IGNORE
    assert decision.reason_code == "invalid_decision_schema"

