"""Validated plugin settings independent of AstrBot's config wrapper."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Tuple

from .relationships import RelationshipEntry, parse_relationships

# Internal pipeline knobs — not exposed in _conf_schema.json.
HISTORY_LIMIT = 100
DECISION_THRESHOLD = 0.72
DEBOUNCE_MIN_SECONDS = 4.0
DEBOUNCE_MAX_SECONDS = 8.0
TOPIC_MAX_SECONDS = 12
HUMANIZE_DELAY_ENABLED = True
MAX_REPLY_SEGMENTS = 2


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


@dataclass(frozen=True)
class PluginSettings:
    enabled_groups: Tuple[str, ...] = ()
    aliases: Tuple[str, ...] = ("爱弥斯", "小爱", "飞行雪绒")
    decision_provider: str = ""
    generation_provider: str = ""
    vision_provider: str = ""
    persona_id: str = ""
    persona_prompt: str = ""
    spontaneous_hourly_limit: int = 6
    spontaneous_cooldown_seconds: int = 600
    vision_enabled: bool = True
    handle_native_wake: bool = True
    continuation_seconds: int = 90
    relationships: Tuple[RelationshipEntry, ...] = ()
    # Internal (hardcoded) — kept on the dataclass for policy wiring.
    history_limit: int = HISTORY_LIMIT
    decision_threshold: float = DECISION_THRESHOLD
    debounce_min_seconds: float = DEBOUNCE_MIN_SECONDS
    debounce_max_seconds: float = DEBOUNCE_MAX_SECONDS
    topic_max_seconds: int = TOPIC_MAX_SECONDS
    humanize_delay_enabled: bool = HUMANIZE_DELAY_ENABLED
    max_reply_segments: int = MAX_REPLY_SEGMENTS

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PluginSettings":
        # Legacy keys for internal knobs are ignored on purpose.
        return cls(
            enabled_groups=_string_tuple(raw.get("enabled_groups", ())),
            aliases=_string_tuple(
                raw.get("aliases", ("爱弥斯", "小爱", "飞行雪绒")),
                ("爱弥斯", "小爱", "飞行雪绒"),
            ),
            decision_provider=str(raw.get("decision_provider", "") or "").strip(),
            generation_provider=str(raw.get("generation_provider", "") or "").strip(),
            vision_provider=str(raw.get("vision_provider", "") or "").strip(),
            persona_id=str(raw.get("persona_id", "") or "").strip(),
            persona_prompt=str(raw.get("persona_prompt", "") or "").strip(),
            spontaneous_hourly_limit=_bounded_int(
                raw.get("spontaneous_hourly_limit", 6), 6, 1, 60
            ),
            spontaneous_cooldown_seconds=_bounded_int(
                raw.get("spontaneous_cooldown_seconds", 600), 600, 0, 7200
            ),
            vision_enabled=_boolean(raw.get("vision_enabled", True), True),
            handle_native_wake=_boolean(raw.get("handle_native_wake", True), True),
            continuation_seconds=_bounded_int(
                raw.get("continuation_seconds", 90), 90, 0, 600
            ),
            relationships=parse_relationships(raw.get("relationships")),
            history_limit=HISTORY_LIMIT,
            decision_threshold=DECISION_THRESHOLD,
            debounce_min_seconds=DEBOUNCE_MIN_SECONDS,
            debounce_max_seconds=DEBOUNCE_MAX_SECONDS,
            topic_max_seconds=TOPIC_MAX_SECONDS,
            humanize_delay_enabled=HUMANIZE_DELAY_ENABLED,
            max_reply_segments=MAX_REPLY_SEGMENTS,
        )
