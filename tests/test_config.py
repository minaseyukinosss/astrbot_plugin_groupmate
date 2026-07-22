from groupmate.config import PluginSettings


def test_defaults_are_balanced_and_safe():
    settings = PluginSettings.from_mapping({})

    assert settings.history_limit == 100
    assert settings.spontaneous_hourly_limit == 6
    assert settings.decision_threshold >= 0.7
    assert settings.enabled_groups == ()
    assert settings.continuation_seconds == 90
    assert settings.topic_max_seconds == 12
    assert settings.humanize_delay_enabled is True
    assert settings.max_reply_segments == 2
    assert settings.handle_native_wake is True
    assert len(settings.relationships) == 2
    assert settings.relationships[0].sender_id == "674852406"


def test_invalid_ranges_are_clamped():
    settings = PluginSettings.from_mapping(
        {
            "history_limit": 9999,
            "decision_threshold": -2,
            "max_reply_segments": 9,
            "topic_max_seconds": 999,
        }
    )

    assert settings.history_limit == 500
    assert settings.decision_threshold == 0.0
    assert settings.max_reply_segments == 3
    assert settings.topic_max_seconds == 60


def test_boolean_strings_are_parsed_safely():
    disabled = PluginSettings.from_mapping(
        {"handle_native_wake": "false", "humanize_delay_enabled": "0"}
    )
    enabled = PluginSettings.from_mapping(
        {"handle_native_wake": "true", "humanize_delay_enabled": "yes"}
    )
    assert disabled.handle_native_wake is False
    assert disabled.humanize_delay_enabled is False
    assert enabled.handle_native_wake is True
    assert enabled.humanize_delay_enabled is True


def test_aliases_and_group_ids_are_normalized():
    settings = PluginSettings.from_mapping(
        {"aliases": [" 小爱 ", "", "爱弥斯"], "enabled_groups": [123, "456"]}
    )

    assert settings.aliases == ("小爱", "爱弥斯")
    assert settings.enabled_groups == ("123", "456")
