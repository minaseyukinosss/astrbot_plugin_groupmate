import asyncio
from types import SimpleNamespace

import pytest

from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.settings import SocialRuntimeSettings
from groupmate.social_runtime.contracts import RuntimeMode


class _MessageObject:
    self_id = "bot-1"

    def __init__(
        self, message_id: str, text: str, *, segments: list[dict] | None = None
    ) -> None:
        self.raw_message = {
            "message_id": message_id,
            "group_id": "g-1",
            "user_id": "42",
            "time": 10,
            "sender": {"nickname": "小夏", "card": "夏夏"},
            "message": segments or [{"type": "text", "data": {"text": text}}],
        }


class _FakeAstrEvent:
    def __init__(
        self, message_id: str, text: str, *, segments: list[dict] | None = None
    ) -> None:
        self.message_obj = _MessageObject(message_id, text, segments=segments)
        self.message_str = text
        self.stop_calls = 0

    def stop_event(self):
        self.stop_calls += 1

    @staticmethod
    def get_group_id():
        return "g-1"

    @staticmethod
    def get_sender_id():
        return "42"

    @staticmethod
    def get_sender_name():
        return "夏夏"


class _FakeManager:
    def __init__(self, mode: RuntimeMode = RuntimeMode.SHADOW) -> None:
        self.ingested = []
        self.mode = mode

    async def ingest(self, event):
        self.ingested.append(event)
        return SimpleNamespace(inserted=False)

    def group_mode(self, _group_id):
        return self.mode


def _bridge_for(tmp_path, *, mode: RuntimeMode = RuntimeMode.SHADOW):
    settings = SocialRuntimeSettings(
        enabled_groups=("g-1",),
        social_runtime_test_groups=(),
        runtime_mode=mode.value,
        generation_provider="fake-provider",
        vision_provider="",
        persona_id="groupmate:default",
        persona_name="爱弥斯",
        persona_aliases=("小爱",),
        external_command_prefixes=("bq=astrbot.meme",),
    )
    bridge = AstrBotSocialRuntimeBridge(object(), settings, tmp_path, clock=lambda: 20)
    bridge._manager = _FakeManager(mode)
    bridge._started = True
    return bridge


def _text(text: str) -> list[dict]:
    return [{"type": "text", "data": {"text": text}}]


def _at_bot(text: str) -> list[dict]:
    return [
        {"type": "at", "data": {"qq": "bot-1"}},
        *_text(text),
    ]


def _reply_to_bot(text: str) -> list[dict]:
    return [
        {"type": "reply", "data": {"id": "previous", "sender_id": "bot-1"}},
        *_text(text),
    ]


@pytest.mark.parametrize(
    ("mode", "message", "segments", "expected_stops"),
    (
        (RuntimeMode.SOCIAL_RUNTIME, "你好", _at_bot("你好"), 1),
        (RuntimeMode.SOCIAL_RUNTIME, "小爱在吗", _text("小爱在吗"), 1),
        (RuntimeMode.SOCIAL_RUNTIME, "然后呢", _reply_to_bot("然后呢"), 1),
        (RuntimeMode.SHADOW, "你好", _at_bot("你好"), 0),
        (RuntimeMode.OFF, "你好", _at_bot("你好"), 0),
        (RuntimeMode.SOCIAL_RUNTIME, "bq 开心", _at_bot("bq 开心"), 0),
        (RuntimeMode.SOCIAL_RUNTIME, "大家好", _text("大家好"), 0),
    ),
)
def test_bridge_claims_only_production_direct_groupmate_messages(
    tmp_path, mode, message, segments, expected_stops
):
    bridge = _bridge_for(tmp_path, mode=mode)
    event = _FakeAstrEvent("claim", message, segments=segments)

    asyncio.run(bridge.handle_event(event))

    assert event.stop_calls == expected_stops


def test_early_observer_records_facts_without_ingesting(tmp_path):
    bridge = _bridge_for(tmp_path)

    asyncio.run(bridge.observe_event(_FakeAstrEvent("9", "bq 熊猫头")))

    view = bridge.trace_repository.query(
        persona_id="groupmate:default", group_id="g-1"
    )
    assert len(view["items"]) == 1
    assert view["items"][0]["summary"]["route"]["owner"] == "EXTERNAL_PLUGIN"
    assert bridge.manager.ingested == []


def test_low_priority_handler_marks_the_existing_trace_entered(tmp_path):
    bridge = _bridge_for(tmp_path)
    event = _FakeAstrEvent("10", "大家晚上好")

    asyncio.run(bridge.observe_event(event))
    asyncio.run(bridge.handle_event(event))

    view = bridge.trace_repository.query(
        persona_id="groupmate:default", group_id="g-1"
    )
    assert len(view["items"]) == 1
    assert view["items"][0]["summary"]["route"]["owner"] == "GROUPMATE"
    assert len(bridge.manager.ingested) == 1


def test_trace_uses_astrbot_resolved_name_for_previously_unseen_at_member(tmp_path):
    class AtComponent:
        qq = "123"
        name = "仲手天尊"

        @staticmethod
        def toDict():
            return {"type": "at", "data": {"qq": "123"}}

    event = _FakeAstrEvent("11", "昨")
    event.message_obj.raw_message["message"] = [
        {"type": "at", "data": {"qq": "123"}},
        {"type": "text", "data": {"text": "昨"}},
    ]
    event.message_obj.message = [AtComponent()]
    bridge = _bridge_for(tmp_path)

    asyncio.run(bridge.observe_event(event))

    message = bridge.trace_repository.query(
        persona_id="groupmate:default", group_id="g-1"
    )["items"][0]["summary"]["message"]
    assert message["parts"][0]["label"] == "@仲手天尊"
    assert message["parts"][0]["display_name"] == "仲手天尊"
    assert "123" not in str(message)
