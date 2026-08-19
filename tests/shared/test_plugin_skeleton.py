"""Observable behavior of the Phase A composition-root skeleton."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.settings import SocialRuntimeSettings


def test_default_settings_are_off_and_use_a_new_database():
    settings = SocialRuntimeSettings.from_mapping({})

    assert settings.runtime_mode == "OFF"
    assert settings.database_name == "groupmate-social-runtime-v2.db"
    assert settings.control_admin_ids == ()


def test_control_administrators_are_an_explicit_trimmed_deployment_allowlist():
    settings = SocialRuntimeSettings.from_mapping(
        {"control_admin_ids": [" admin:root ", "", "ops:two"]}
    )

    assert settings.control_admin_ids == ("admin:root", "ops:two")

    root = Path(__file__).parents[2]
    schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))
    composition = (root / "main.py").read_text(encoding="utf-8")
    assert schema["control_admin_ids"]["default"] == []
    assert "admin_ids=(username,)" not in composition
    assert "admin_ids=self.settings.control_admin_ids" in composition


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
