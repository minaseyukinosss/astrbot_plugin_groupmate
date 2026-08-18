"""Conservative memory consolidation reports without direct policy mutation."""

from __future__ import annotations

from dataclasses import dataclass

from .pipeline import MemoryRecord


@dataclass(frozen=True)
class ConsolidationReport:
    duplicate_groups: tuple[tuple[str, ...], ...]
    conflicting_memory_ids: tuple[str, ...]
    calibration_candidates: tuple[dict[str, object], ...]
    direct_policy_changes: tuple[str, ...]
    generated_at: int


class MemoryConsolidator:
    def consolidate(
        self, records: tuple[MemoryRecord, ...], *, now: int
    ) -> ConsolidationReport:
        by_content: dict[tuple[str, str, str], list[str]] = {}
        for record in records:
            key = (record.persona_id, record.group_id, record.content_hash)
            by_content.setdefault(key, []).append(record.memory_id)
        duplicates = tuple(
            tuple(sorted(ids))
            for _, ids in sorted(by_content.items())
            if len(ids) > 1
        )
        conflicts = tuple(sorted(item.memory_id for item in records if item.conflict))
        return ConsolidationReport(
            duplicate_groups=duplicates,
            conflicting_memory_ids=conflicts,
            calibration_candidates=(),
            direct_policy_changes=(),
            generated_at=int(now),
        )


__all__ = ("ConsolidationReport", "MemoryConsolidator")
