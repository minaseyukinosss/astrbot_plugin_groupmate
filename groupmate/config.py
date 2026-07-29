"""Validated plugin settings independent of AstrBot's config wrapper."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Tuple

from .core.relationships import RelationshipEntry, parse_relationships

# Internal pipeline knobs — not exposed in _conf_schema.json.
HISTORY_LIMIT = 100
DEBOUNCE_MIN_SECONDS = 4.0
DEBOUNCE_MAX_SECONDS = 8.0
TOPIC_MAX_SECONDS = 12
HUMANIZE_DELAY_ENABLED = True
MAX_REPLY_SEGMENTS = 2
DEFAULT_MAX_REPLY_CHARS = 60
DEFAULT_ALIASES = ("爱弥斯", "小爱", "飞行雪绒")

_NESTED_GROUPS = (
    "wake_group",
    "persona_group",
    "relationship_group",
    "provider_group",
    "media_group",
    "limits_group",
)


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _string_tuple(value: Any, default: Tuple[str, ...] = ()) -> Tuple[str, ...]:
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = default
    result = []
    for item in values:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _boolean(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1", "yes", "on"):
            return True
        if normalized in ("false", "0", "no", "off", ""):
            return False
    if value is None:
        return default
    return bool(value)


def _as_mapping(raw: Any) -> Mapping[str, Any]:
    if isinstance(raw, Mapping):
        return raw
    return {}


def flatten_plugin_config(raw: Mapping[str, Any]) -> dict:
    """Flatten nested AstrBot schema groups; keep legacy flat keys as fallback."""
    flat: dict = {}
    for group in _NESTED_GROUPS:
        nested = raw.get(group)
        if isinstance(nested, Mapping):
            for key, value in nested.items():
                flat[key] = value
    for key, value in raw.items():
        if key in _NESTED_GROUPS:
            continue
        if key not in flat:
            flat[key] = value
    return flat


@dataclass(frozen=True)
class PluginSettings:
    enabled_groups: Tuple[str, ...] = ()
    aliases: Tuple[str, ...] = DEFAULT_ALIASES
    generation_provider: str = ""
    vision_provider: str = ""
    max_reply_chars: int = DEFAULT_MAX_REPLY_CHARS
    spontaneous_hourly_limit: int = 6
    spontaneous_cooldown_seconds: int = 600
    vision_enabled: bool = True
    handle_native_wake: bool = True
    continuation_seconds: int = 90
    relationships: Tuple[RelationshipEntry, ...] = ()
    group_brief: str = ""
    history_limit: int = HISTORY_LIMIT
    debounce_min_seconds: float = DEBOUNCE_MIN_SECONDS
    debounce_max_seconds: float = DEBOUNCE_MAX_SECONDS
    topic_max_seconds: int = TOPIC_MAX_SECONDS
    humanize_delay_enabled: bool = HUMANIZE_DELAY_ENABLED
    max_reply_segments: int = MAX_REPLY_SEGMENTS
    v3_scheduler_enabled: bool = True
    v3_opportunity_enabled: bool = True
    v3_memory_writer_enabled: bool = True
    v3_composition_enabled: bool = True
    reaction_media_enabled: bool = False
    reaction_catalog_path: str = ""

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PluginSettings":
        data = flatten_plugin_config(_as_mapping(raw))
        reaction_catalog_path = str(
            data.get("reaction_catalog_path", "") or ""
        ).strip()
        return cls(
            enabled_groups=_string_tuple(data.get("enabled_groups", ())),
            aliases=_string_tuple(
                data.get("aliases", DEFAULT_ALIASES),
                DEFAULT_ALIASES,
            ),
            generation_provider=str(data.get("generation_provider", "") or "").strip(),
            vision_provider=str(data.get("vision_provider", "") or "").strip(),
            max_reply_chars=_bounded_int(
                data.get("max_reply_chars", DEFAULT_MAX_REPLY_CHARS),
                DEFAULT_MAX_REPLY_CHARS,
                20,
                200,
            ),
            spontaneous_hourly_limit=_bounded_int(
                data.get("spontaneous_hourly_limit", 6), 6, 1, 60
            ),
            spontaneous_cooldown_seconds=_bounded_int(
                data.get("spontaneous_cooldown_seconds", 600), 600, 0, 7200
            ),
            vision_enabled=_boolean(data.get("vision_enabled", True), True),
            handle_native_wake=_boolean(data.get("handle_native_wake", True), True),
            continuation_seconds=_bounded_int(
                data.get("continuation_seconds", 90), 90, 0, 600
            ),
            relationships=parse_relationships(data.get("relationships"), defaults=()),
            group_brief=str(data.get("group_brief", "") or "").strip(),
            history_limit=HISTORY_LIMIT,
            debounce_min_seconds=DEBOUNCE_MIN_SECONDS,
            debounce_max_seconds=DEBOUNCE_MAX_SECONDS,
            topic_max_seconds=TOPIC_MAX_SECONDS,
            humanize_delay_enabled=HUMANIZE_DELAY_ENABLED,
            max_reply_segments=MAX_REPLY_SEGMENTS,
            v3_scheduler_enabled=_boolean(
                data.get("v3_scheduler_enabled", True), True
            ),
            v3_opportunity_enabled=_boolean(
                data.get("v3_opportunity_enabled", True), True
            ),
            v3_memory_writer_enabled=_boolean(
                data.get("v3_memory_writer_enabled", True), True
            ),
            v3_composition_enabled=_boolean(
                data.get("v3_composition_enabled", True), True
            ),
            reaction_media_enabled=(
                _boolean(data.get("reaction_media_enabled", False), False)
                and bool(reaction_catalog_path)
            ),
            reaction_catalog_path=reaction_catalog_path,
        )
