"""Validated plugin settings independent of AstrBot's config wrapper."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Tuple

from .relationships import RelationshipEntry, parse_relationships


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _bounded_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        return max(low, min(high, float(value)))
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
    history_limit: int = 100
    decision_threshold: float = 0.72
    spontaneous_hourly_limit: int = 6
    spontaneous_cooldown_seconds: int = 600
    debounce_min_seconds: float = 4.0
    debounce_max_seconds: float = 8.0
    topic_max_seconds: int = 12
    vision_enabled: bool = True
    handle_native_wake: bool = True
    continuation_seconds: int = 90
    humanize_delay_enabled: bool = True
    max_reply_segments: int = 2
    relationships: Tuple[RelationshipEntry, ...] = ()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PluginSettings":
        minimum_debounce = _bounded_float(raw.get("debounce_min_seconds", 4), 4.0, 0.0, 30.0)
        maximum_debounce = _bounded_float(
            raw.get("debounce_max_seconds", 8), 8.0, minimum_debounce, 30.0
        )
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
            history_limit=_bounded_int(raw.get("history_limit", 100), 100, 1, 500),
            decision_threshold=_bounded_float(
                raw.get("decision_threshold", 0.72), 0.72, 0.0, 1.0
            ),
            spontaneous_hourly_limit=_bounded_int(
                raw.get("spontaneous_hourly_limit", 6), 6, 1, 60
            ),
            spontaneous_cooldown_seconds=_bounded_int(
                raw.get("spontaneous_cooldown_seconds", 600), 600, 0, 7200
            ),
            debounce_min_seconds=minimum_debounce,
            debounce_max_seconds=maximum_debounce,
            topic_max_seconds=_bounded_int(raw.get("topic_max_seconds", 12), 12, 1, 60),
            vision_enabled=_boolean(raw.get("vision_enabled", True), True),
            handle_native_wake=_boolean(raw.get("handle_native_wake", True), True),
            continuation_seconds=_bounded_int(
                raw.get("continuation_seconds", 90), 90, 0, 600
            ),
            humanize_delay_enabled=_boolean(
                raw.get("humanize_delay_enabled", True), True
            ),
            max_reply_segments=_bounded_int(
                raw.get("max_reply_segments", 2), 2, 1, 3
            ),
            relationships=parse_relationships(raw.get("relationships")),
        )
