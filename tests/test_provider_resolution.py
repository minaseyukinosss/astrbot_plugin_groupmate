import asyncio

from groupmate.host.bridge import AstrBotBridge
from groupmate.host.config import AstrBotConfigParser
from tests.test_native_wake_suppress import _FakeEvent


class ProviderContext:
    def __init__(self, current_provider="group-provider"):
        self.current_provider = current_provider
        self.current_provider_calls = 0

    async def get_current_chat_provider_id(self, umo):
        assert umo
        self.current_provider_calls += 1
        return self.current_provider


def settings(*, generation_provider="", vision_provider="", vision_enabled=True):
    return AstrBotConfigParser().parse(
        {
            "persona_group": {
                "persona_aliases": {"aemeath": ["爱弥斯", "小爱"]},
                "relationships": {"aemeath": []},
            },
            "provider_group": {
                "generation_provider": generation_provider,
                "vision_provider": vision_provider,
                "vision_enabled": vision_enabled,
            },
        }
    )


def test_explicit_generation_provider_wins_over_current_group_provider(tmp_path):
    async def scenario():
        context = ProviderContext()
        bridge = AstrBotBridge(
            context,
            settings(generation_provider="fixed-provider"),
            tmp_path,
        )
        await bridge._prepare_actor(_FakeEvent(group_id="g1"))
        resolved = bridge._provider_by_group["g1"]
        calls = context.current_provider_calls
        await bridge.close()
        return resolved, calls

    resolved, calls = asyncio.run(scenario())
    assert resolved == "fixed-provider"
    assert calls == 0


def test_empty_generation_provider_follows_group_provider(tmp_path):
    async def scenario():
        context = ProviderContext()
        bridge = AstrBotBridge(context, settings(), tmp_path)
        await bridge._prepare_actor(_FakeEvent(group_id="g1"))
        resolved = bridge._provider_by_group["g1"]
        calls = context.current_provider_calls
        await bridge.close()
        return resolved, calls

    resolved, calls = asyncio.run(scenario())
    assert resolved == "group-provider"
    assert calls == 1


def test_empty_vision_provider_reuses_resolved_text_provider(tmp_path):
    context = ProviderContext()
    bridge = AstrBotBridge(context, settings(), tmp_path)
    bridge._provider_by_group["g1"] = "group-provider"
    workflow = bridge._workflow_for("g1", bridge.persona_context)

    assert workflow.vision.provider_getter("g1") == "group-provider"
    asyncio.run(bridge.close())


def test_disabled_vision_is_registered_but_unavailable(tmp_path):
    bridge = AstrBotBridge(
        ProviderContext(),
        settings(vision_enabled=False),
        tmp_path,
    )
    workflow = bridge._workflow_for("g1", bridge.persona_context)

    spec = workflow.capabilities.lookup("vision")
    assert spec is not None
    assert spec.available is False
    assert workflow.capability_governor is not None
    asyncio.run(bridge.close())


def test_status_reports_health_without_removed_values(tmp_path):
    bridge = AstrBotBridge(ProviderContext(), settings(), tmp_path)

    payload = bridge.status()

    assert payload["active_persona"] == "aemeath"
    assert payload["enabled_scope"] == "all"
    assert payload["generation_provider_mode"] == "current_group"
    assert payload["vision_status"] == "reuse_text"
    assert "group_brief" not in repr(payload)
    assert "max_reply_chars" not in repr(payload)
    asyncio.run(bridge.close())
