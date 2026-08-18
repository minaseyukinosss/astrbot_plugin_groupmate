"""Observable behavior of the Phase A composition-root skeleton."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from groupmate.adapters.astrbot_bridge import (
    AstrBotSocialRuntimeBridge,
    RuntimeNotReadyError,
)
from groupmate.settings import SocialRuntimeSettings


def test_default_settings_are_off_and_use_a_new_database():
    settings = SocialRuntimeSettings.from_mapping({})

    assert settings.runtime_mode == "OFF"
    assert settings.database_name == "groupmate-social-runtime-v2.db"


def test_foundation_bridge_fails_closed_until_runtime_exists(tmp_path: Path):
    bridge = AstrBotSocialRuntimeBridge(
        context=object(),
        settings=SocialRuntimeSettings.from_mapping({}),
        data_dir=tmp_path,
    )

    with pytest.raises(RuntimeNotReadyError, match="foundation incomplete"):
        asyncio.run(bridge.start())
