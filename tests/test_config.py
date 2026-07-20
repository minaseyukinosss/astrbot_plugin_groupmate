from groupmate.config import PluginSettings


def test_defaults_are_balanced_and_safe():
    settings = PluginSettings.from_mapping({})

    assert settings.history_limit == 100
    assert settings.spontaneous_hourly_limit == 6
    assert settings.decision_threshold >= 0.7
    assert settings.enabled_groups == ()
    assert settings.shadow_mode is False
    assert settings.shadow_sample_rate == 1.0
    assert settings.shadow_retention_days == 7
    assert settings.shadow_store_message_text is False


def test_invalid_ranges_are_clamped():
    settings = PluginSettings.from_mapping(
        {
            "history_limit": 9999,
            "decision_threshold": -2,
            "shadow_sample_rate": 3,
            "shadow_retention_days": 999,
        }
    )

    assert settings.history_limit == 500
    assert settings.decision_threshold == 0.0
    assert settings.shadow_sample_rate == 1.0
    assert settings.shadow_retention_days == 30


def test_shadow_boolean_strings_are_parsed_safely():
    disabled = PluginSettings.from_mapping(
        {"shadow_mode": "false", "shadow_store_message_text": "0"}
    )
    enabled = PluginSettings.from_mapping(
        {"shadow_mode": "true", "shadow_store_message_text": "yes"}
    )
    assert disabled.shadow_mode is False
    assert disabled.shadow_store_message_text is False
    assert enabled.shadow_mode is True
    assert enabled.shadow_store_message_text is True


def test_aliases_and_group_ids_are_normalized():
    settings = PluginSettings.from_mapping(
        {"aliases": [" 小爱 ", "", "爱弥斯"], "enabled_groups": [123, "456"]}
    )

    assert settings.aliases == ("小爱", "爱弥斯")
    assert settings.enabled_groups == ("123", "456")
