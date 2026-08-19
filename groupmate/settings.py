"""Deployment settings owned by Social Runtime v2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class SocialRuntimeSettings:
    enabled_groups: tuple[str, ...]
    social_runtime_test_groups: tuple[str, ...]
    runtime_mode: str
    generation_provider: str
    vision_provider: str
    database_name: str
    persona_id: str
    bot_qq: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object] | None) -> "SocialRuntimeSettings":
        source = dict(raw or {})
        return cls(
            enabled_groups=tuple(
                str(value).strip()
                for value in source.get("enabled_groups", ())
                if str(value).strip()
            ),
            social_runtime_test_groups=tuple(
                str(value).strip()
                for value in source.get("social_runtime_test_groups", ())
                if str(value).strip()
            ),
            runtime_mode=str(source.get("runtime_mode", "OFF") or "OFF").upper(),
            generation_provider=str(source.get("generation_provider", "") or "").strip(),
            vision_provider=str(source.get("vision_provider", "") or "").strip(),
            database_name="groupmate-social-runtime-v2.db",
            persona_id=str(source.get("persona_id", "aemeath") or "aemeath").strip(),
            bot_qq=str(source.get("bot_qq", "323537051") or "323537051").strip(),
        )
