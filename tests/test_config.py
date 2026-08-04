import json
from dataclasses import fields
from pathlib import Path

import pytest

from groupmate.host.config import (
    AstrBotConfigParser,
    ConfigurationError,
    DeploymentSettings,
)


ROOT = Path(__file__).resolve().parents[1]


def test_deployment_settings_contain_only_seven_public_values():
    settings = AstrBotConfigParser().parse({})

    assert {item.name for item in fields(DeploymentSettings)} == {
        "enabled_groups",
        "persona_aliases",
        "relationships",
        "generation_provider",
        "vision_enabled",
        "vision_provider",
        "poke_enabled",
        "diagnostics",
    }
    assert settings.enabled_groups == ()
    assert settings.aliases_for("aemeath") == ("爱弥斯", "小爱", "飞行雪绒")
    assert settings.relationships_for("aemeath") == ()
    assert settings.poke_enabled is False


def test_poke_enabled_is_nested_and_explicit():
    settings = AstrBotConfigParser().parse(
        {"interaction_group": {"poke_enabled": True}}
    )

    assert settings.poke_enabled is True
    assert settings.diagnostics.unknown_keys == ()


@pytest.mark.parametrize(
    "value",
    ("flase", "true", {"enabled": True}, [True], 1),
)
def test_invalid_poke_enabled_values_fail_closed(value):
    settings = AstrBotConfigParser().parse(
        {"interaction_group": {"poke_enabled": value}}
    )

    assert settings.poke_enabled is False


def test_removed_configuration_and_fallbacks_are_absent_from_production():
    removed = (
        "PluginSettings",
        "GroupPolicy",
        "handle_native_wake",
        "group_brief",
        "max_reply_chars",
        "spontaneous_hourly_limit",
        "spontaneous_cooldown_seconds",
        "v3_scheduler_enabled",
        "v3_memory_writer_enabled",
        "v3_composition_enabled",
        "reaction_media_enabled",
        "reaction_catalog_path",
        "LocalReactionCatalog",
        "ReactionPolicy",
        "DEFAULT_RELATIONSHIPS",
        "flatten_plugin_config",
    )
    production = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "groupmate").rglob("*.py")
    )

    for name in removed:
        assert name not in production


def test_explicit_empty_aliases_are_not_replaced_by_defaults():
    settings = AstrBotConfigParser().parse(
        {
            "persona_group": {
                "persona_aliases": {"aemeath": []},
                "relationships": {"aemeath": []},
            }
        }
    )

    assert settings.aliases_for("aemeath") == ()
    assert "empty_aliases:aemeath" in settings.diagnostics.warnings


def test_duplicate_relationship_ids_are_rejected():
    raw = {
        "persona_group": {
            "relationships": {
                "aemeath": [
                    {"id": "123", "relationship": "普通群友", "address": ""},
                    {"id": "123", "relationship": "闺蜜", "address": "小明"},
                ]
            }
        }
    }

    with pytest.raises(ConfigurationError, match=r"persona_group.relationships.aemeath.*123"):
        AstrBotConfigParser().parse(raw)


def test_legacy_keys_are_diagnosed_and_never_applied():
    settings = AstrBotConfigParser().parse(
        {"group_brief": "旧值", "max_reply_chars": 999, "enabled_groups": ["100"]}
    )

    assert settings.enabled_groups == ()
    assert settings.diagnostics.ignored_legacy_keys == ("enabled_groups",)
    assert settings.diagnostics.unknown_keys == (
        "group_brief",
        "max_reply_chars",
    )


def test_group_and_member_ids_are_strict_digits():
    settings = AstrBotConfigParser().parse(
        {
            "scope_group": {"enabled_groups": ["100", 200]},
            "persona_group": {
                "relationships": {
                    "aemeath": [
                        {"id": "323537051", "relationship": "最亲近", "address": "小维"}
                    ]
                }
            },
        }
    )

    assert settings.enabled_groups == ("100", "200")
    assert settings.relationships_for("aemeath")[0].sender_id == "323537051"

    with pytest.raises(ConfigurationError, match=r"scope_group.enabled_groups"):
        AstrBotConfigParser().parse({"scope_group": {"enabled_groups": ["abc"]}})
    with pytest.raises(ConfigurationError, match=r"persona_group.relationships.aemeath"):
        AstrBotConfigParser().parse(
            {
                "persona_group": {
                    "relationships": {
                        "aemeath": [
                            {"id": "abc", "relationship": "普通群友", "address": ""}
                        ]
                    }
                }
            }
        )


def test_relationship_labels_are_limited_to_supported_levels():
    with pytest.raises(ConfigurationError, match=r"relationship"):
        AstrBotConfigParser().parse(
            {
                "persona_group": {
                    "relationships": {
                        "aemeath": [
                            {"id": "123", "relationship": "主人", "address": ""}
                        ]
                    }
                }
            }
        )


def test_unknown_top_level_keys_are_diagnostics_only():
    settings = AstrBotConfigParser().parse(
        {
            "scope_group": {"enabled_groups": ["100"]},
            "unknown_group": {"enabled_groups": ["999"]},
            "random_key": True,
        }
    )

    assert settings.enabled_groups == ("100",)
    assert settings.diagnostics.unknown_keys == ("random_key", "unknown_group")


def test_schema_exposes_exactly_seven_settings():
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    items = {
        name
        for group in schema.values()
        for name in group.get("items", {})
    }

    assert items == {
        "enabled_groups",
        "persona_aliases",
        "relationships",
        "generation_provider",
        "vision_enabled",
        "vision_provider",
        "poke_enabled",
    }
    assert schema["scope_group"]["items"]["enabled_groups"]["default"] == []
    assert (
        schema["interaction_group"]["items"]["poke_enabled"]["default"]
        is False
    )
