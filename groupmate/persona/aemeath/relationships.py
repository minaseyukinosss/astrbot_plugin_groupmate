"""爱弥斯默认关系映射（可被插件配置覆盖）。"""

from __future__ import annotations

from typing import Any, Tuple

from ...core.relationships import (
    RelationshipEntry,
    parse_relationships as parse_relationship_entries,
    relationship_map,
    resolve_speaker,
)

DEFAULT_RELATIONSHIPS: Tuple[RelationshipEntry, ...] = (
    RelationshipEntry("674852406", "最亲近", "Minase"),
    RelationshipEntry("1634104393", "闺蜜", ""),
)


def parse_relationships(raw: Any) -> Tuple[RelationshipEntry, ...]:
    return parse_relationship_entries(raw, defaults=DEFAULT_RELATIONSHIPS)


__all__ = [
    "DEFAULT_RELATIONSHIPS",
    "RelationshipEntry",
    "parse_relationships",
    "relationship_map",
    "resolve_speaker",
]
