import json
from pathlib import Path

from groupmate.config import (
    HISTORY_LIMIT,
    HUMANIZE_DELAY_ENABLED,
    MAX_REPLY_SEGMENTS,
    TOPIC_MAX_SECONDS,
    PluginSettings,
    flatten_plugin_config,
)
from groupmate.core.relationships import RelationshipEntry


def test_defaults_are_balanced_and_safe():
    settings = PluginSettings.from_mapping({})

    assert settings.history_limit == HISTORY_LIMIT
    assert settings.spontaneous_hourly_limit == 6
    assert settings.enabled_groups == ()
    assert settings.continuation_seconds == 90
    assert settings.topic_max_seconds == TOPIC_MAX_SECONDS
    assert settings.humanize_delay_enabled is HUMANIZE_DELAY_ENABLED
    assert settings.max_reply_segments == MAX_REPLY_SEGMENTS
    assert settings.handle_native_wake is True
    assert settings.relationships == ()
    assert settings.character_name == "爱弥斯"
    assert settings.max_reply_chars == 60
    assert settings.v3_scheduler_enabled is True
    assert settings.v3_social_enabled is True
    assert settings.v3_opportunity_enabled is True
    assert settings.v3_memory_writer_enabled is True
    assert settings.v3_composition_enabled is True
    assert settings.reaction_media_enabled is False
    assert settings.reaction_catalog_path == ""


def test_internal_knobs_ignore_legacy_config_keys():
    settings = PluginSettings.from_mapping(
        {
            "history_limit": 9999,
            "decision_threshold": -2,
            "max_reply_segments": 9,
            "topic_max_seconds": 999,
            "debounce_min_seconds": 0.1,
            "debounce_max_seconds": 0.2,
            "humanize_delay_enabled": False,
        }
    )

    assert settings.history_limit == HISTORY_LIMIT
    assert settings.max_reply_segments == MAX_REPLY_SEGMENTS
    assert settings.topic_max_seconds == TOPIC_MAX_SECONDS
    assert settings.humanize_delay_enabled is HUMANIZE_DELAY_ENABLED
    assert not hasattr(settings, "decision_threshold")


def test_boolean_strings_are_parsed_safely():
    disabled = PluginSettings.from_mapping({"handle_native_wake": "false"})
    enabled = PluginSettings.from_mapping({"handle_native_wake": "true"})
    assert disabled.handle_native_wake is False
    assert enabled.handle_native_wake is True


def test_aliases_and_group_ids_are_normalized():
    settings = PluginSettings.from_mapping(
        {"aliases": [" 小爱 ", "", "爱弥斯"], "enabled_groups": [123, "456"]}
    )

    assert settings.aliases == ("小爱", "爱弥斯")
    assert settings.enabled_groups == ("123", "456")


def test_nested_schema_groups_are_flattened():
    settings = PluginSettings.from_mapping(
        {
            "wake_group": {
                "aliases": ["小爱"],
                "enabled_groups": ["100"],
                "handle_native_wake": False,
                "continuation_seconds": 30,
                "v3_scheduler_enabled": False,
                "v3_social_enabled": False,
                "v3_opportunity_enabled": False,
                "v3_memory_writer_enabled": False,
            },
            "persona_group": {
                "character_name": "飞行雪绒",
                "group_brief": "这个群爱抽卡",
                "max_reply_chars": 48,
                "persona_prompt": "自定义人格",
            },
            "provider_group": {
                "vision_enabled": False,
                "generation_provider": "gpt",
            },
            "media_group": {
                "v3_composition_enabled": False,
                "reaction_media_enabled": True,
                "reaction_catalog_path": " /srv/groupmate/reactions ",
            },
            "limits_group": {
                "spontaneous_hourly_limit": 3,
                "spontaneous_cooldown_seconds": 120,
            },
        }
    )
    assert settings.aliases == ("小爱",)
    assert settings.enabled_groups == ("100",)
    assert settings.handle_native_wake is False
    assert settings.continuation_seconds == 30
    assert settings.v3_scheduler_enabled is False
    assert settings.v3_social_enabled is False
    assert settings.v3_opportunity_enabled is False
    assert settings.v3_memory_writer_enabled is False
    assert settings.character_name == "飞行雪绒"
    assert settings.group_brief == "这个群爱抽卡"
    assert settings.max_reply_chars == 48
    assert settings.persona_prompt == "自定义人格"
    assert settings.vision_enabled is False
    assert settings.generation_provider == "gpt"
    assert settings.v3_composition_enabled is False
    assert settings.reaction_media_enabled is True
    assert settings.reaction_catalog_path == "/srv/groupmate/reactions"
    assert settings.spontaneous_hourly_limit == 3
    assert settings.spontaneous_cooldown_seconds == 120


def test_template_list_relationships_are_parsed():
    settings = PluginSettings.from_mapping(
        {
            "relationship_group": {
                "relationships": [
                    {
                        "__template_key": "member",
                        "id": "674852406",
                        "relationship": "最亲近",
                        "address": "Minase",
                    },
                    {
                        "__template_key": "member",
                        "id": "1634104393",
                        "relationship": "闺蜜",
                        "address": "",
                    },
                ]
            }
        }
    )
    assert settings.relationships == (
        RelationshipEntry("674852406", "最亲近", "Minase"),
        RelationshipEntry("1634104393", "闺蜜", ""),
    )


def test_legacy_flat_keys_still_work():
    settings = PluginSettings.from_mapping(
        {
            "aliases": ["爱弥斯"],
            "persona_prompt": "旧扁平覆盖",
            "relationships": [
                {"id": "1", "relationship": "闺蜜", "address": "A"},
            ],
            "max_reply_chars": 80,
        }
    )
    assert settings.aliases == ("爱弥斯",)
    assert settings.persona_prompt == "旧扁平覆盖"
    assert settings.relationships[0].sender_id == "1"
    assert settings.max_reply_chars == 80


def test_max_reply_chars_is_bounded():
    assert PluginSettings.from_mapping({"max_reply_chars": 5}).max_reply_chars == 20
    assert PluginSettings.from_mapping({"max_reply_chars": 999}).max_reply_chars == 200


def test_flatten_prefers_nested_over_duplicate_flat():
    flat = flatten_plugin_config(
        {
            "aliases": ["扁平"],
            "wake_group": {"aliases": ["嵌套"]},
        }
    )
    assert flat["aliases"] == ["嵌套"]


def test_reaction_media_cannot_enable_without_catalog_path():
    settings = PluginSettings.from_mapping(
        {"reaction_media_enabled": True, "reaction_catalog_path": "  "}
    )

    assert settings.reaction_media_enabled is False
    assert settings.reaction_catalog_path == ""


def test_schema_exposes_composition_and_reaction_controls():
    schema_path = Path(__file__).resolve().parents[1] / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    items = schema["media_group"]["items"]

    assert items["v3_composition_enabled"]["default"] is True
    assert items["reaction_media_enabled"]["default"] is False
    assert items["reaction_catalog_path"]["default"] == ""
