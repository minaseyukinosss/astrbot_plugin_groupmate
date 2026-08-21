import asyncio
from types import SimpleNamespace

from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.settings import SocialRuntimeSettings


class _MessageObject:
    self_id = "bot-1"

    def __init__(self, message_id: str, text: str) -> None:
        self.raw_message = {
            "message_id": message_id,
            "group_id": "g-1",
            "user_id": "42",
            "time": 10,
            "sender": {"nickname": "小夏", "card": "夏夏"},
            "message": [{"type": "text", "data": {"text": text}}],
        }


class _FakeAstrEvent:
    def __init__(self, message_id: str, text: str) -> None:
        self.message_obj = _MessageObject(message_id, text)
        self.message_str = text

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
    def __init__(self) -> None:
        self.ingested = []

    async def ingest(self, event):
        self.ingested.append(event)
        return SimpleNamespace(inserted=False)


def _bridge_for(tmp_path):
    settings = SocialRuntimeSettings(
        enabled_groups=("g-1",),
        social_runtime_test_groups=(),
        runtime_mode="SHADOW",
        generation_provider="fake-provider",
        vision_provider="",
        persona_id="groupmate:default",
        external_command_prefixes=("bq=astrbot.meme",),
    )
    bridge = AstrBotSocialRuntimeBridge(object(), settings, tmp_path, clock=lambda: 20)
    bridge._manager = _FakeManager()
    bridge._started = True
    return bridge


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
