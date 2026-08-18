from __future__ import annotations

import asyncio

from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.settings import SocialRuntimeSettings
from groupmate.social_runtime.manager import NoSideEffectExecutionPort


def _raw_message():
    return {
        "message_id": "m1",
        "group_id": "885617919",
        "user_id": "42",
        "time": 1700000000,
        "sender": {"nickname": "小夏"},
        "message": [{"type": "text", "data": {"text": "大家早"}}],
    }


def test_shadow_persists_and_projects_without_side_effects(tmp_path):
    async def scenario():
        bridge = AstrBotSocialRuntimeBridge(
            context=object(),
            settings=SocialRuntimeSettings.from_mapping(
                {
                    "runtime_mode": "SHADOW",
                    "enabled_groups": ["885617919"],
                }
            ),
            data_dir=tmp_path,
        )
        await bridge.start()
        await bridge.handle_event(_raw_message())
        await bridge.manager.drain()
        state = await bridge.manager.group_snapshot("885617919")
        event_ids = bridge.manager.event_store.event_ids()
        execution = bridge.manager.execution_port
        await bridge.close()
        return event_ids, state, execution

    event_ids, state, execution = asyncio.run(scenario())

    assert event_ids == ("qq:m1",)
    assert state.scene_version == 1
    assert isinstance(execution, NoSideEffectExecutionPort)
    assert execution.calls == ()


def test_off_mode_does_not_create_database_or_translate_event(tmp_path):
    async def scenario():
        bridge = AstrBotSocialRuntimeBridge(
            context=object(),
            settings=SocialRuntimeSettings.from_mapping({}),
            data_dir=tmp_path,
        )
        await bridge.start()
        assert await bridge.handle_event(object()) is None
        await bridge.close()

    asyncio.run(scenario())

    assert not (tmp_path / "groupmate-social-runtime-v2.db").exists()
