"""Observable behavior of the Phase A composition-root skeleton."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.settings import SOCIAL_RUNTIME_DATABASE_NAME, SocialRuntimeSettings


def test_default_settings_are_off_and_database_is_plugin_owned():
    settings = SocialRuntimeSettings.from_mapping({})

    assert settings.runtime_mode == "OFF"
    assert SOCIAL_RUNTIME_DATABASE_NAME == "groupmate-social-runtime-v2.db"
    assert not hasattr(settings, "database_name")
    assert settings.control_admin_ids == ()
    assert settings.external_command_prefixes == ()
    assert settings.external_link_domains == ()


def test_astrbot_config_only_exposes_groupmate_deployment_choices():
    root = Path(__file__).parents[2]
    schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))

    visible = {
        name
        for name, definition in schema.items()
        if definition.get("invisible") is not True
    }

    assert visible == {
        "enabled_groups",
        "runtime_mode",
        "generation_provider",
        "vision_provider",
        "persona_id",
    }
    assert schema["runtime_mode"]["options"] == ["SHADOW", "SOCIAL_RUNTIME"]
    assert schema["runtime_mode"]["labels"] == [
        "SHADOW 观察（不发送）",
        "正式运行（允许发送）",
    ]
    assert schema["generation_provider"]["_special"] == "select_provider"
    assert schema["vision_provider"]["_special"] == "select_provider"
    assert schema["persona_id"]["_special"] == "select_persona"
    assert "bot_qq" not in schema
    assert "database_name" not in schema


def test_complete_native_configuration_enters_no_send_shadow_automatically():
    settings = SocialRuntimeSettings.from_mapping(
        {
            "enabled_groups": [" group-1 "],
            "generation_provider": "provider:text",
            "vision_provider": "provider:vision",
            "persona_id": "persona:groupmate",
        }
    )

    assert settings.enabled_groups == ("group-1",)
    assert settings.generation_provider == "provider:text"
    assert settings.vision_provider == "provider:vision"
    assert settings.persona_id == "persona:groupmate"
    assert settings.runtime_mode == "SHADOW"


def test_native_production_mode_applies_to_every_enabled_group():
    settings = SocialRuntimeSettings.from_mapping(
        {
            "enabled_groups": [" group-1 ", "group-2"],
            "runtime_mode": "SOCIAL_RUNTIME",
            "generation_provider": "provider:text",
            "persona_id": "persona:groupmate",
        }
    )

    assert settings.runtime_mode == "SOCIAL_RUNTIME"
    assert settings.social_runtime_test_groups == ("group-1", "group-2")


def test_control_administrators_are_internal_governance_state():
    settings = SocialRuntimeSettings.from_mapping(
        {"control_admin_ids": [" admin:root ", "", "ops:two"]}
    )

    assert settings.control_admin_ids == ("admin:root", "ops:two")

    root = Path(__file__).parents[2]
    schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))
    composition = (root / "main.py").read_text(encoding="utf-8")
    assert "control_admin_ids" not in schema
    assert "admin_ids=self.settings.control_admin_ids or (username,)" in composition
    assert "EventMessageType.GROUP_MESSAGE, priority=-100" in composition


def test_external_trigger_rules_are_not_first_run_configuration():
    settings = SocialRuntimeSettings.from_mapping(
        {
            "external_command_prefixes": [" xw=astrbot.waves ", ""],
            "external_link_domains": [
                " v.douyin.com=astrbot.video_parser ",
                " ",
            ],
        }
    )

    assert settings.external_command_prefixes == ("xw=astrbot.waves",)
    assert settings.external_link_domains == (
        "v.douyin.com=astrbot.video_parser",
    )

    root = Path(__file__).parents[2]
    schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))
    assert "external_command_prefixes" not in schema
    assert "external_link_domains" not in schema


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
