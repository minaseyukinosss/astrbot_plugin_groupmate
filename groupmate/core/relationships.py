"""通用发言者关系映射（产品无关）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Sequence, Tuple


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
