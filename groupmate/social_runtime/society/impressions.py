"""Scoped impressions with expiry, evidence, and durable tombstone identity."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Impression:
    impression_id: str
    persona_id: str
    group_id: str
    subject_id: str
    statement: str
    evidence_event_ids: tuple[str, ...]
    status: str
    expires_at: int | None
    use_scope: tuple[str, str, str]
    content_hash: str


class ImpressionRegistry:
    def __init__(self) -> None:
        self._items: dict[str, Impression] = {}
        self._tombstones: set[str] = set()

    def propose(
        self,
        *,
        persona_id: str,
        group_id: str,
        subject_id: str,
        statement: str,
        evidence_event_ids: tuple[str, ...],
        expires_at: int | None,
    ) -> Impression | None:
        if not persona_id or not group_id or not subject_id or not evidence_event_ids:
            raise ValueError("impression scope and evidence are required")
        normalized = re.sub(r"\s+", " ", statement.strip()).casefold()
        if not normalized:
            raise ValueError("impression statement is required")
        content_hash = hashlib.sha256(
            f"{persona_id}\0{group_id}\0{subject_id}\0{normalized}".encode()
        ).hexdigest()
        if content_hash in self._tombstones:
            return None
        impression_id = f"impression:{content_hash[:24]}"
        existing = self._items.get(impression_id)
        if existing is not None:
            return existing
        impression = Impression(
            impression_id=impression_id,
            persona_id=persona_id,
            group_id=group_id,
            subject_id=subject_id,
            statement=statement.strip(),
            evidence_event_ids=tuple(evidence_event_ids),
            status="candidate",
            expires_at=expires_at,
            use_scope=(persona_id, group_id, subject_id),
            content_hash=content_hash,
        )
        self._items[impression_id] = impression
        return impression

    def tombstone(self, impression_id: str) -> Impression:
        current = self._items[impression_id]
        deleted = replace(current, status="tombstoned")
        self._items[impression_id] = deleted
        self._tombstones.add(current.content_hash)
        return deleted


__all__ = ("Impression", "ImpressionRegistry")
