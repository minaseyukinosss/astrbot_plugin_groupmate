"""Configurable speaker relationship mapping for persona context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class RelationshipEntry:
    sender_id: str
    relationship: str
    address: str = ""


# Built-in Aemeath defaults; overridable via plugin settings.
DEFAULT_RELATIONSHIPS: Tuple[RelationshipEntry, ...] = (
    RelationshipEntry("674852406", "最亲近", "Minase"),
    RelationshipEntry("1634104393", "闺蜜", ""),
)


def parse_relationships(raw: Any) -> Tuple[RelationshipEntry, ...]:
    if raw is None:
        return DEFAULT_RELATIONSHIPS
    if isinstance(raw, Mapping) and not isinstance(raw, (str, bytes)):
        # Allow {sender_id: {relationship, address}} or {sender_id: relationship}
        entries = []
        for sender_id, value in raw.items():
            sid = str(sender_id).strip()
            if not sid:
                continue
            if isinstance(value, Mapping):
                relationship = str(value.get("relationship", "普通群友") or "普通群友").strip()
                address = str(value.get("address", "") or "").strip()
            else:
                relationship = str(value or "普通群友").strip()
                address = ""
            entries.append(RelationshipEntry(sid, relationship or "普通群友", address))
        return tuple(entries) if entries else DEFAULT_RELATIONSHIPS

    if isinstance(raw, (list, tuple)):
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
        return tuple(entries) if entries else DEFAULT_RELATIONSHIPS

    return DEFAULT_RELATIONSHIPS


def relationship_map(
    entries: Sequence[RelationshipEntry],
) -> Dict[str, Tuple[str, str]]:
    return {
        entry.sender_id: (entry.relationship, entry.address) for entry in entries
    }


def resolve_speaker(
    sender_id: str,
    sender_name: str,
    relationships: Optional[Mapping[str, Tuple[str, str]]] = None,
) -> Tuple[str, str, str]:
    mapping = relationships if relationships is not None else relationship_map(
        DEFAULT_RELATIONSHIPS
    )
    sender_id = str(sender_id)
    sender_name = (sender_name or "").strip()
    relationship, fixed_address = mapping.get(sender_id, ("普通群友", ""))
    if not sender_name or sender_name == sender_id:
        speaker = fixed_address or "群友"
    else:
        speaker = sender_name[:80]
    suggested_address = fixed_address or speaker
    return speaker, relationship, suggested_address
