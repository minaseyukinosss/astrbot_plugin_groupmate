from groupmate.config import (
    DECISION_THRESHOLD,
    HISTORY_LIMIT,
    HUMANIZE_DELAY_ENABLED,
    MAX_REPLY_SEGMENTS,
    TOPIC_MAX_SECONDS,
    PluginSettings,
)


def test_defaults_are_balanced_and_safe():
    settings = PluginSettings.from_mapping({})

    assert settings.history_limit == HISTORY_LIMIT
    assert settings.spontaneous_hourly_limit == 6
    assert settings.decision_threshold == DECISION_THRESHOLD
    assert settings.enabled_groups == ()
    assert settings.continuation_seconds == 90
    assert settings.topic_max_seconds == TOPIC_MAX_SECONDS
    assert settings.humanize_delay_enabled is HUMANIZE_DELAY_ENABLED
    assert settings.max_reply_segments == MAX_REPLY_SEGMENTS
    assert settings.handle_native_wake is True
    assert len(settings.relationships) == 2
    assert settings.relationships[0].sender_id == "674852406"


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
    assert settings.decision_threshold == DECISION_THRESHOLD
    assert settings.max_reply_segments == MAX_REPLY_SEGMENTS
    assert settings.topic_max_seconds == TOPIC_MAX_SECONDS
    assert settings.humanize_delay_enabled is HUMANIZE_DELAY_ENABLED


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
