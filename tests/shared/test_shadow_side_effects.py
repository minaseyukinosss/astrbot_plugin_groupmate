from __future__ import annotations

import asyncio

import pytest

from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.settings import SocialRuntimeSettings
from groupmate.social_runtime.manager import (
    NoSideEffectExecutionPort,
    ShadowSideEffectForbidden,
)
from groupmate.social_runtime.manager import PhaseARuntimeModeError, SocialRuntimeManager
from groupmate.social_runtime.contracts import RuntimeMode


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
        outbox_count = bridge.manager.event_store.outbox_count()
        await bridge.close()
        return event_ids, state, execution, outbox_count

    event_ids, state, execution, outbox_count = asyncio.run(scenario())

    assert event_ids == ("qq:m1",)
    assert state.scene_version == 1
    assert isinstance(execution, NoSideEffectExecutionPort)
    assert execution.calls == ()
    assert outbox_count == 0


def test_shadow_execution_port_records_and_rejects_every_attempt():
    async def scenario():
        execution = NoSideEffectExecutionPort()
        action = {"kind": "send", "text": "绝不能发出"}
        with pytest.raises(ShadowSideEffectForbidden):
            await execution.execute(action)
        return execution.calls

    assert asyncio.run(scenario()) == ({"kind": "send", "text": "绝不能发出"},)


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


def test_phase_a_rejects_social_runtime_before_creating_database(tmp_path):
    async def bridge_scenario():
        bridge = AstrBotSocialRuntimeBridge(
            context=object(),
            settings=SocialRuntimeSettings.from_mapping(
                {
                    "runtime_mode": "SOCIAL_RUNTIME",
                    "enabled_groups": ["885617919"],
                }
            ),
            data_dir=tmp_path,
        )
        with pytest.raises(PhaseARuntimeModeError, match="only supports SHADOW"):
            await bridge.start()

    asyncio.run(bridge_scenario())
    assert not (tmp_path / "groupmate-social-runtime-v2.db").exists()


def test_direct_off_manager_construction_is_rejected_without_io(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    with pytest.raises(PhaseARuntimeModeError, match="only supports SHADOW"):
        SocialRuntimeManager(
            database_path=path,
            persona_id="aemeath",
            mode=RuntimeMode.OFF,
            enabled_groups=("885617919",),
        )
    assert not path.exists()
