"""Strict AstrBot deployment configuration for Groupmate.

This module reads AstrBot-owned installation settings. Most runtime behavior
stays code-owned; a curated poke advanced subset may override InteractionPolicy
defaults from WebUI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence, Tuple

from ..core.relationships import RelationshipEntry
from ..policies import InteractionPolicy


DEFAULT_PERSONA_ID = "aemeath"
DEFAULT_ALIASES = ("爱弥斯", "小爱", "飞行雪绒")
RELATIONSHIP_LABELS = ("普通群友", "闺蜜", "最亲近")

_KNOWN_GROUPS = (
    "scope_group",
    "persona_group",
    "provider_group",
    "interaction_group",
    "tools_group",
)
_LEGACY_TOP_LEVEL_KEYS = (
    "aliases",
    "continuation_seconds",
    "debounce_max_seconds",
    "debounce_min_seconds",
    "direct_pressure_nudge_count",
    "direct_pressure_pester_count",
    "direct_pressure_window_seconds",
    "enabled_groups",
    "history_limit",
    "humanize_delay_enabled",
    "max_reply_segments",
    "relationships",
    "topic_max_seconds",
    "vision_enabled",
    "vision_provider",
    "generation_provider",
)


class ConfigurationError(ValueError):
    """Raised when host configuration is malformed."""


@dataclass(frozen=True)
class ConfigDiagnostics:
    """Non-fatal parser diagnostics for ignored deployment input."""

    ignored_legacy_keys: Tuple[str, ...] = ()
    unknown_keys: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DeploymentSettings:
    """Immutable deployment settings parsed from AstrBot config."""

    enabled_groups: Tuple[str, ...]
    persona_aliases: Tuple[Tuple[str, Tuple[str, ...]], ...]
    relationships: Tuple[Tuple[str, Tuple[RelationshipEntry, ...]], ...]
    generation_provider: str
    vision_enabled: bool
    vision_provider: str
    poke_enabled: bool
    poke_back_enabled: bool
    poke_exclusive: bool
    poke_face_enabled: bool
    poke_react_probability: float
    poke_cooldown_seconds: int
    poke_back_probability: float
    poke_bystander_probability: float
    poke_bystander_cooldown_seconds: int
    poke_face_probability: float
    tools_enabled: bool
    command_bridge_enabled: bool
    tool_candidate_limit: int
    diagnostics: ConfigDiagnostics

    def aliases_for(self, persona_id: str) -> Tuple[str, ...]:
        return dict(self.persona_aliases).get(str(persona_id), ())

    def relationships_for(self, persona_id: str) -> Tuple[RelationshipEntry, ...]:
        return dict(self.relationships).get(str(persona_id), ())

    def interaction_policy(self) -> InteractionPolicy:
        defaults = InteractionPolicy()
        face_probability = (
            self.poke_face_probability if self.poke_face_enabled else 0.0
        )
        return InteractionPolicy(
            poke_react_probability=self.poke_react_probability,
            poke_cooldown_seconds=self.poke_cooldown_seconds,
            poke_session_per_minute=defaults.poke_session_per_minute,
            poke_back_probability=self.poke_back_probability,
            poke_only_share=defaults.poke_only_share,
            poke_burst_probability=defaults.poke_burst_probability,
            poke_burst_max=defaults.poke_burst_max,
            poke_interval_seconds=defaults.poke_interval_seconds,
            poke_bystander_probability=self.poke_bystander_probability,
            poke_bystander_cooldown_seconds=self.poke_bystander_cooldown_seconds,
            poke_bystander_target=defaults.poke_bystander_target,
            poke_face_probability=face_probability,
            poke_face_pool=defaults.poke_face_pool,
        )


class AstrBotConfigParser:
    """Parse the public AstrBot settings into immutable deployment state."""

    def parse(self, raw: Mapping[str, Any] | None) -> DeploymentSettings:
        source = _as_mapping(raw)
        diagnostics = _diagnostics_for(source)

        scope_group = _as_mapping(source.get("scope_group"))
        persona_group = _as_mapping(source.get("persona_group"))
        provider_group = _as_mapping(source.get("provider_group"))
        interaction_group = _as_mapping(source.get("interaction_group"))
        tools_group = _as_mapping(source.get("tools_group"))
        defaults = InteractionPolicy()

        enabled_groups = _parse_digit_tuple(
            scope_group.get("enabled_groups", ()),
            path="scope_group.enabled_groups",
        )
        persona_aliases, alias_warnings = _parse_persona_aliases_with_warnings(
            persona_group.get("persona_aliases")
        )
        relationships = _parse_persona_relationships(
            persona_group.get("relationships")
        )
        warnings = tuple(sorted(set(diagnostics.warnings + alias_warnings)))

        return DeploymentSettings(
            enabled_groups=enabled_groups,
            persona_aliases=persona_aliases,
            relationships=relationships,
            generation_provider=str(
                provider_group.get("generation_provider", "") or ""
            ).strip(),
            vision_enabled=_boolean(provider_group.get("vision_enabled", True), True),
            vision_provider=str(provider_group.get("vision_provider", "") or "").strip(),
            poke_enabled=_strict_boolean(
                interaction_group.get("poke_enabled", False),
                False,
            ),
            poke_back_enabled=_strict_boolean(
                interaction_group.get("poke_back_enabled", False),
                False,
            ),
            poke_exclusive=_strict_boolean(
                interaction_group.get("poke_exclusive", False),
                False,
            ),
            poke_face_enabled=_strict_boolean(
                interaction_group.get("poke_face_enabled", False),
                False,
            ),
            poke_react_probability=_float_clamped(
                interaction_group.get("poke_react_probability"),
                defaults.poke_react_probability,
                0.0,
                1.0,
            ),
            poke_cooldown_seconds=_int_clamped(
                interaction_group.get("poke_cooldown_seconds"),
                defaults.poke_cooldown_seconds,
                0,
                120,
            ),
            poke_back_probability=_float_clamped(
                interaction_group.get("poke_back_probability"),
                defaults.poke_back_probability,
                0.0,
                1.0,
            ),
            poke_bystander_probability=_float_clamped(
                interaction_group.get("poke_bystander_probability"),
                defaults.poke_bystander_probability,
                0.0,
                1.0,
            ),
            poke_bystander_cooldown_seconds=_int_clamped(
                interaction_group.get("poke_bystander_cooldown_seconds"),
                defaults.poke_bystander_cooldown_seconds,
                0,
                300,
            ),
            poke_face_probability=_float_clamped(
                interaction_group.get("poke_face_probability"),
                0.12,
                0.0,
                1.0,
            ),
            tools_enabled=_strict_boolean(
                tools_group.get("enabled", True),
                True,
            ),
            command_bridge_enabled=_strict_boolean(
                tools_group.get("command_bridge_enabled", True),
                True,
            ),
            tool_candidate_limit=_int_clamped(
                tools_group.get("candidate_limit"),
                8,
                1,
                20,
            ),
            diagnostics=ConfigDiagnostics(
                ignored_legacy_keys=diagnostics.ignored_legacy_keys,
                unknown_keys=diagnostics.unknown_keys,
                warnings=warnings,
            ),
        )


def _diagnostics_for(raw: Mapping[str, Any]) -> ConfigDiagnostics:
    ignored = []
    unknown = []
    for key in raw:
        if key in _KNOWN_GROUPS:
            continue
        if key in _LEGACY_TOP_LEVEL_KEYS:
            ignored.append(key)
        else:
            unknown.append(key)
    return ConfigDiagnostics(
        ignored_legacy_keys=tuple(sorted(ignored)),
        unknown_keys=tuple(sorted(unknown)),
    )


def _as_mapping(raw: Any) -> Mapping[str, Any]:
    if isinstance(raw, Mapping):
        return raw
    return {}


def _boolean(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off", ""}:
            return False
    if value is None:
        return default
    return bool(value)


def _strict_boolean(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _float_clamped(value: Any, default: float, low: float, high: float) -> float:
    if value is None or value == "":
        return float(default)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return max(float(low), min(float(high), number))


def _int_clamped(value: Any, default: int, low: int, high: int) -> int:
    if value is None or value == "":
        return int(default)
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return int(default)
    return max(int(low), min(int(high), number))


def _parse_digit_tuple(raw: Any, *, path: str) -> Tuple[str, ...]:
    if raw is None or raw == "":
        return ()
    if isinstance(raw, str):
        items: Sequence[Any] = [raw]
    elif isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
        items = raw
    else:
        raise ConfigurationError(f"{path} must be a list of digit strings")

    result = []
    for index, item in enumerate(items):
        text = str(item).strip()
        if not text:
            continue
        if not text.isdigit():
            raise ConfigurationError(f"{path}[{index}] must be digits: {text}")
        if text not in result:
            result.append(text)
    return tuple(result)


def _parse_persona_aliases_with_warnings(
    raw: Any,
) -> Tuple[Tuple[Tuple[str, Tuple[str, ...]], ...], Tuple[str, ...]]:
    warnings = []
    if raw is None:
        return ((DEFAULT_PERSONA_ID, DEFAULT_ALIASES),), ()
    if not isinstance(raw, Mapping):
        raise ConfigurationError("persona_group.persona_aliases must be an object")

    result = []
    for persona_id in sorted(str(key).strip() for key in raw if str(key).strip()):
        values = raw[persona_id]
        aliases = _parse_text_tuple(
            values,
            path=f"persona_group.persona_aliases.{persona_id}",
        )
        if not aliases:
            warnings.append(f"empty_aliases:{persona_id}")
        result.append((persona_id, aliases))
    return tuple(result), tuple(warnings)


def _parse_text_tuple(raw: Any, *, path: str) -> Tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        values: Sequence[Any] = [raw]
    elif isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
        values = raw
    else:
        raise ConfigurationError(f"{path} must be a list of strings")

    result = []
    for item in values:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _parse_persona_relationships(
    raw: Any,
) -> Tuple[Tuple[str, Tuple[RelationshipEntry, ...]], ...]:
    if raw is None:
        return ((DEFAULT_PERSONA_ID, ()),)
    if not isinstance(raw, Mapping):
        raise ConfigurationError("persona_group.relationships must be an object")

    result = []
    for persona_id in sorted(str(key).strip() for key in raw if str(key).strip()):
        entries = _parse_relationship_entries(
            raw[persona_id],
            path=f"persona_group.relationships.{persona_id}",
        )
        result.append((persona_id, entries))
    return tuple(result)


def _parse_relationship_entries(raw: Any, *, path: str) -> Tuple[RelationshipEntry, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise ConfigurationError(f"{path} must be a list")

    seen = set()
    entries = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ConfigurationError(f"{path}[{index}] must be an object")
        sender_id = str(item.get("id") or item.get("sender_id") or "").strip()
        if not sender_id or not sender_id.isdigit():
            raise ConfigurationError(f"{path}[{index}].id must be digits")
        if sender_id in seen:
            raise ConfigurationError(f"{path} contains duplicate id {sender_id}")
        seen.add(sender_id)

        relationship = str(item.get("relationship") or "普通群友").strip()
        if relationship not in RELATIONSHIP_LABELS:
            raise ConfigurationError(
                f"{path}[{index}].relationship unsupported: {relationship}"
            )
        address = str(item.get("address") or "").strip()
        entries.append(RelationshipEntry(sender_id, relationship, address))
    return tuple(entries)
