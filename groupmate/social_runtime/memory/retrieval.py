"""Scoped, budgeted, structured memory retrieval."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..persistence.repositories import ScopeRequiredError
from .pipeline import MemoryRecord


@dataclass(frozen=True)
class MemoryQuery:
    persona_id: str
    group_id: str
    subject_id: str | None
    text: str
    now: int
    token_budget: int
    allowed_sensitivities: tuple[str, ...]


@dataclass(frozen=True)
class MemoryContextItem:
    memory_id: str
    group_id: str
    subject_id: str | None
    kind: str
    summary: str
    evidence_event_ids: tuple[str, ...]
    confidence: float
    conflict: bool


@dataclass(frozen=True)
class MemoryContextBlock:
    persona_id: str
    group_id: str
    items: tuple[MemoryContextItem, ...]
    tokens_used: int
    conflicting_memory_ids: tuple[str, ...]


class MemoryRetriever:
    def retrieve(
        self, records: tuple[MemoryRecord, ...], query: MemoryQuery
    ) -> MemoryContextBlock:
        if not query.persona_id.strip() or not query.group_id.strip():
            raise ScopeRequiredError("memory query requires persona_id and group_id")
        if query.token_budget < 0:
            raise ValueError("token budget must not be negative")
        scoped = [
            item
            for item in records
            if item.persona_id == query.persona_id
            and item.group_id == query.group_id
            and (query.subject_id is None or item.subject_id == query.subject_id)
            and item.sensitivity in query.allowed_sensitivities
        ]
        ranked = sorted(
            scoped,
            key=lambda item: (-self._score(item, query), item.memory_id),
        )
        selected = []
        used = 0
        seen_kinds = set()
        for record in ranked:
            tokens = max(1, math.ceil(len(record.summary) / 4))
            if used + tokens > query.token_budget:
                continue
            diversity_penalty = record.kind in seen_kinds and len(selected) >= 2
            if diversity_penalty:
                continue
            selected.append(
                MemoryContextItem(
                    memory_id=record.memory_id,
                    group_id=record.group_id,
                    subject_id=record.subject_id,
                    kind=record.kind,
                    summary=record.summary,
                    evidence_event_ids=record.evidence_event_ids,
                    confidence=record.confidence,
                    conflict=record.conflict,
                )
            )
            used += tokens
            seen_kinds.add(record.kind)
        conflicts = tuple(item.memory_id for item in selected if item.conflict)
        return MemoryContextBlock(
            query.persona_id, query.group_id, tuple(selected), used, conflicts
        )

    @staticmethod
    def _score(record: MemoryRecord, query: MemoryQuery) -> float:
        query_chars = set(query.text.casefold())
        memory_chars = set(record.normalized_content)
        relevance = (
            len(query_chars & memory_chars) / max(1, len(query_chars))
            if query_chars
            else 0.0
        )
        age_days = max(0, query.now - record.occurred_at) / 86400
        recency = 1.0 / (1.0 + age_days)
        return relevance * 3 + recency + record.confidence + record.importance


__all__ = ("MemoryContextBlock", "MemoryContextItem", "MemoryQuery", "MemoryRetriever")
