"""Observable behavior of the Phase A composition-root skeleton."""

from __future__ import annotations

import asyncio
from pathlib import Path

from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.settings import SocialRuntimeSettings


def test_default_settings_are_off_and_use_a_new_database():
    settings = SocialRuntimeSettings.from_mapping({})

    assert settings.runtime_mode == "OFF"
    assert settings.database_name == "groupmate-social-runtime-v2.db"


def test_off_bridge_starts_and_stops_without_creating_runtime_data(tmp_path: Path):
    bridge = AstrBotSocialRuntimeBridge(
        context=object(),
        settings=SocialRuntimeSettings.from_mapping({}),
        data_dir=tmp_path,
    )

    async def scenario():
        await bridge.start()
        await bridge.close()

    asyncio.run(scenario())

    assert not (tmp_path / "groupmate-social-runtime-v2.db").exists()
