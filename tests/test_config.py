from groupmate.config import PluginSettings


def test_defaults_are_balanced_and_safe():
    settings = PluginSettings.from_mapping({})

    assert settings.history_limit == 100
    assert settings.spontaneous_hourly_limit == 6
    assert settings.decision_threshold >= 0.7
    assert settings.enabled_groups == ()


def test_invalid_ranges_are_clamped():
    settings = PluginSettings.from_mapping(
        {"history_limit": 9999, "decision_threshold": -2}
    )

    assert settings.history_limit == 500
    assert settings.decision_threshold == 0.0


def test_aliases_and_group_ids_are_normalized():
    settings = PluginSettings.from_mapping(
        {"aliases": [" 小爱 ", "", "爱弥斯"], "enabled_groups": [123, "456"]}
    )

    assert settings.aliases == ("小爱", "爱弥斯")
    assert settings.enabled_groups == ("123", "456")

