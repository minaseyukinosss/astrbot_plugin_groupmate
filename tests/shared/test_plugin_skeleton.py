"""Observable behavior of the Phase A composition-root skeleton."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.adapters.astrbot_models import AstrBotModelPort
from groupmate.settings import (
    DEFAULT_GROUPMATE_PERSONA_ID,
    SOCIAL_RUNTIME_DATABASE_NAME,
    SocialRuntimeSettings,
)
from groupmate.social_runtime.cognition.ambient_worker import DirectAmbientWorker


def test_default_settings_are_off_and_database_is_plugin_owned():
    settings = SocialRuntimeSettings.from_mapping({})

    assert settings.runtime_mode == "OFF"
    assert settings.persona_id == DEFAULT_GROUPMATE_PERSONA_ID
    assert SOCIAL_RUNTIME_DATABASE_NAME == "groupmate-social-runtime-v2.db"
    assert not hasattr(settings, "database_name")
    assert settings.control_admin_ids == ()
    assert settings.external_command_prefixes == ()
    assert settings.external_link_domains == ()
    assert settings.cognition_timeout_seconds == 8
    assert settings.cognition_api_key == ""
    assert settings.cognition_api_base == "https://api.deepseek.com"
    assert settings.cognition_model == "deepseek-v4-flash"
    assert settings.persona_name == "Groupmate"
    assert settings.persona_aliases == ()
    assert settings.persona_preset == "aemeath_current"
    assert settings.profile_enabled is True
    assert settings.profile_batch_messages == 20
    assert settings.profile_batch_interval_seconds == 600
    assert settings.profile_timeout_seconds == 30


def test_persona_identity_settings_normalize_confirmed_aliases():
    settings = SocialRuntimeSettings.from_mapping(
        {
            "persona_name": " 爱弥斯 ",
            "persona_aliases": [" 小爱 ", "爱弥斯", "小爱", ""],
        }
    )

    assert settings.persona_name == "爱弥斯"
    assert settings.persona_aliases == ("小爱",)


def test_unknown_persona_preset_is_rejected():
    with pytest.raises(ValueError, match="persona_preset"):
        SocialRuntimeSettings.from_mapping({"persona_preset": "unknown"})


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
        "cognition_api_key",
        "cognition_api_base",
        "cognition_model",
        "vision_provider",
        "cognition_timeout_seconds",
        "external_command_prefixes",
        "external_link_domains",
        "persona_name",
        "persona_aliases",
        "persona_preset",
        "profile_enabled",
        "profile_batch_messages",
        "profile_batch_interval_seconds",
        "profile_timeout_seconds",
    }
    assert schema["runtime_mode"]["options"] == ["SHADOW", "SOCIAL_RUNTIME"]
    assert schema["runtime_mode"]["labels"] == [
        "SHADOW 观察（不发送）",
        "正式运行（允许发送）",
    ]
    assert schema["generation_provider"]["_special"] == "select_provider"
    assert schema["vision_provider"]["_special"] == "select_provider"
    assert schema["cognition_api_key"]["type"] == "string"
    assert schema["cognition_api_key"]["obvious_hint"] is True
    assert schema["cognition_api_base"]["default"] == "https://api.deepseek.com"
    assert schema["cognition_model"]["default"] == "deepseek-v4-flash"
    assert schema["persona_preset"]["default"] == "aemeath_current"
    assert "persona_id" not in schema
    assert "bot_qq" not in schema
    assert "database_name" not in schema
    assert schema["cognition_timeout_seconds"]["default"] == 8
    assert schema["cognition_timeout_seconds"]["slider"] == {
        "min": 3,
        "max": 15,
        "step": 1,
    }
    assert schema["profile_enabled"]["default"] is True
    assert schema["profile_batch_messages"]["default"] == 20
    assert schema["profile_batch_interval_seconds"]["default"] == 600
    assert schema["profile_timeout_seconds"]["default"] == 30


def test_profile_background_settings_use_safe_bounds():
    settings = SocialRuntimeSettings.from_mapping(
        {
            "profile_enabled": False,
            "profile_batch_messages": 8,
            "profile_batch_interval_seconds": 300,
            "profile_timeout_seconds": 45,
        }
    )

    assert settings.profile_enabled is False
    assert settings.profile_batch_messages == 8
    assert settings.profile_batch_interval_seconds == 300
    assert settings.profile_timeout_seconds == 45

    for values in (
        {"profile_batch_messages": 0},
        {"profile_batch_messages": 21},
        {"profile_batch_interval_seconds": 59},
        {"profile_timeout_seconds": 121},
    ):
        with pytest.raises(ValueError):
            SocialRuntimeSettings.from_mapping(values)


def test_direct_cognition_settings_are_normalized_and_secret_repr_is_redacted():
    settings = SocialRuntimeSettings.from_mapping(
        {
            "cognition_api_key": "  sk-test-secret  ",
            "cognition_api_base": "https://api.deepseek.com/",
            "cognition_model": " deepseek-v4-flash ",
        }
    )

    assert settings.cognition_api_key == "sk-test-secret"
    assert settings.cognition_api_base == "https://api.deepseek.com"
    assert settings.cognition_model == "deepseek-v4-flash"
    assert "sk-test-secret" not in repr(settings)
    assert "cognition_api_key" not in repr(settings)


@pytest.mark.parametrize(
    "base",
    (
        "",
        "ftp://api.deepseek.com",
        "https://",
        "https://api.deepseek.com/chat?debug=1",
        "https://api.deepseek.com/#fragment",
    ),
)
def test_direct_cognition_base_rejects_unsafe_urls(base):
    with pytest.raises(ValueError, match="cognition_api_base"):
        SocialRuntimeSettings.from_mapping({"cognition_api_base": base})


def test_composition_root_reports_each_model_configuration_blocker():
    root = Path(__file__).parents[2]
    composition = (root / "main.py").read_text(encoding="utf-8")

    assert "未选择最终回复模型" in composition
    assert "未配置认知模型 API Key" in composition
    assert "认知模型 API 地址无效" in composition
    assert "未配置认知模型名称" in composition


def test_cognition_timeout_is_configurable_with_safe_bounds():
    settings = SocialRuntimeSettings.from_mapping(
        {"cognition_timeout_seconds": 12}
    )

    assert settings.cognition_timeout_seconds == 12

    for invalid in (0, 2, 16):
        try:
            SocialRuntimeSettings.from_mapping(
                {"cognition_timeout_seconds": invalid}
            )
        except ValueError as exc:
            assert "cognition_timeout_seconds" in str(exc)
        else:
            raise AssertionError("unsafe cognition timeout was accepted")


def test_previous_erroneous_twenty_second_default_migrates_to_eight():
    settings = SocialRuntimeSettings.from_mapping(
        {"cognition_timeout_seconds": 20}
    )

    assert settings.cognition_timeout_seconds == 8


def test_bridge_threads_cognition_timeout_into_runtime_budget(tmp_path: Path):
    settings = SocialRuntimeSettings.from_mapping(
        {
            "enabled_groups": ["group-1"],
            "generation_provider": "provider:text",
            "cognition_timeout_seconds": 12,
        }
    )
    bridge = AstrBotSocialRuntimeBridge(object(), settings, tmp_path)

    async def scenario():
        await bridge.start()
        timeout = bridge.manager.cognition.budget.worker_timeout_seconds
        await bridge.close()
        return timeout

    assert asyncio.run(scenario()) == 12


def test_bridge_registers_one_combined_ambient_model_worker(tmp_path: Path):
    settings = SocialRuntimeSettings.from_mapping(
        {
            "enabled_groups": ["group-1"],
            "generation_provider": "provider:text",
        }
    )
    bridge = AstrBotSocialRuntimeBridge(object(), settings, tmp_path)

    async def scenario():
        await bridge.start()
        workers = dict(bridge.manager.cognition.workers)
        await bridge.close()
        return workers

    workers = asyncio.run(scenario())
    assert "ambient_social_assessor" in workers
    assert isinstance(workers["ambient_social_assessor"], DirectAmbientWorker)
    assert "scene_interpreter" not in workers
    assert "participation_assessor" not in workers
    assert "direct_interaction" not in workers


def test_bridge_owns_direct_client_but_keeps_astrbot_for_final_reply(tmp_path):
    class FakeDirectClient:
        model = "deepseek-v4-flash"

        def __init__(self):
            self.close_calls = 0

        def input_bytes(self, _facts):
            return 1

        async def close(self):
            self.close_calls += 1

    client = FakeDirectClient()
    settings = SocialRuntimeSettings.from_mapping(
        {
            "enabled_groups": ["group-1"],
            "generation_provider": "provider:reply",
            "cognition_api_key": "sk-test",
        }
    )
    bridge = AstrBotSocialRuntimeBridge(
        object(),
        settings,
        tmp_path,
        cognition_client_factory=lambda received: (
            client if received is settings else None
        ),
    )

    async def scenario():
        await bridge.start()
        worker = bridge.manager.cognition.workers["ambient_social_assessor"]
        reply_model = bridge._reply_executor.model
        await bridge.close()
        await bridge.close()
        return worker, reply_model

    worker, reply_model = asyncio.run(scenario())

    assert isinstance(worker, DirectAmbientWorker)
    assert isinstance(reply_model, AstrBotModelPort)
    assert reply_model.provider_id == "provider:reply"
    assert client.close_calls == 1


def test_off_bridge_does_not_create_direct_client(tmp_path):
    calls = []
    bridge = AstrBotSocialRuntimeBridge(
        object(),
        SocialRuntimeSettings.from_mapping({}),
        tmp_path,
        cognition_client_factory=lambda settings: calls.append(settings),
    )

    async def scenario():
        await bridge.start()
        await bridge.close()

    asyncio.run(scenario())

    assert calls == []


def test_complete_native_configuration_enters_no_send_shadow_automatically():
    settings = SocialRuntimeSettings.from_mapping(
        {
            "enabled_groups": [" group-1 "],
            "generation_provider": "provider:text",
            "vision_provider": "provider:vision",
        }
    )

    assert settings.enabled_groups == ("group-1",)
    assert settings.generation_provider == "provider:text"
    assert settings.vision_provider == "provider:vision"
    assert settings.persona_id == DEFAULT_GROUPMATE_PERSONA_ID
    assert settings.runtime_mode == "SHADOW"


def test_native_production_mode_applies_to_every_enabled_group():
    settings = SocialRuntimeSettings.from_mapping(
        {
            "enabled_groups": [" group-1 ", "group-2"],
            "runtime_mode": "SOCIAL_RUNTIME",
            "generation_provider": "provider:text",
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


def test_affection_query_is_claimed_before_chat_and_has_text_fallback():
    root = Path(__file__).parents[2]
    composition = (root / "main.py").read_text(encoding="utf-8")

    assert composition.index("prepare_affection_query") < composition.index(
        "await self.bridge.handle_event(event)"
    )
    assert "await self.bridge.prepare_affection_query(event)" in composition
    assert "event.stop_event()" in composition
    assert "await self.html_render(" in composition
    assert '"full_page": True' in composition
    assert '"clip": {' not in composition
    assert '"scale": "device"' in composition
    assert '"device_scale_factor_level": "ultra"' in composition
    assert "event.image_result" in composition
    assert "event.plain_result(query.text_fallback)" in composition


def test_external_trigger_rules_are_native_deployment_configuration():
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
    assert schema["external_command_prefixes"]["type"] == "list"
    assert schema["external_link_domains"]["type"] == "list"


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


def test_bridge_wakes_due_ambient_attention_without_another_message(tmp_path):
    class FakeManager:
        def __init__(self):
            self.drained_at = []

        async def next_attention_deadline(self):
            return 100

        async def drain(self, *, now=None):
            self.drained_at.append(now)
            bridge._manager = None
            return ()

    bridge = AstrBotSocialRuntimeBridge(
        context=object(),
        settings=SocialRuntimeSettings.from_mapping({}),
        data_dir=tmp_path,
        clock=lambda: 100,
    )
    manager = FakeManager()
    bridge._manager = manager

    asyncio.run(bridge._attention_wakeup_loop(manager))

    assert manager.drained_at == [100]
