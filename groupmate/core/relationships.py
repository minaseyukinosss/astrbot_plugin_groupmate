"""通用发言者关系映射（产品无关）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class RelationshipEntry:
    sender_id: str
    relationship: str
    address: str = ""


def relationship_map(
    entries: Sequence[RelationshipEntry],
) -> Dict[str, Tuple[str, str]]:
    return {entry.sender_id: (entry.relationship, entry.address) for entry in entries}


def resolve_speaker(
    sender_id: str,
    sender_name: str,
    relationships: Optional[Mapping[str, Tuple[str, str]]] = None,
) -> Tuple[str, str, str]:
    mapping = relationships if relationships is not None else {}
    sender_id = str(sender_id)
    sender_name = (sender_name or "").strip()
    relationship, fixed_address = mapping.get(sender_id, ("普通群友", ""))
    if not sender_name or sender_name == sender_id:
        speaker = fixed_address or "群友"
    else:
        speaker = sender_name[:80]
    suggested_address = fixed_address or speaker
    return speaker, relationship, suggested_address


def parse_relationships(
    raw: Any,
    *,
    defaults: Sequence[RelationshipEntry] = (),
) -> Tuple[RelationshipEntry, ...]:
    """解析配置中的关系表；无效或空时回退到 defaults。"""
    fallback = tuple(defaults)

    if raw is None:
        return fallback
    if isinstance(raw, Mapping) and not isinstance(raw, (str, bytes)):
        entries = []
        for sender_id, value in raw.items():
            sid = str(sender_id).strip()
            if not sid:
                continue
            if isinstance(value, Mapping):
                relationship = str(
                    value.get("relationship", "普通群友") or "普通群友"
                ).strip()
                address = str(value.get("address", "") or "").strip()
            else:
                relationship = str(value or "普通群友").strip()
                address = ""
            entries.append(RelationshipEntry(sid, relationship or "普通群友", address))
        return tuple(entries) if entries else fallback

    if isinstance(raw, (list, tuple)):
        if raw and hasattr(raw[0], "sender_id"):
            return tuple(raw)
        entries = []
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            sid = str(item.get("id") or item.get("sender_id") or "").strip()
            if not sid:
                continue
            relationship = str(item.get("relationship", "普通群友") or "普通群友").strip()
            address = str(item.get("address", "") or "").strip()
            entries.append(RelationshipEntry(sid, relationship or "普通群友", address))
        return tuple(entries) if entries else fallback

    return fallback
