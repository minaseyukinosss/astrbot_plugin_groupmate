"""Conservative memory consolidation reports without direct policy mutation."""

from __future__ import annotations

from dataclasses import dataclass

from ..society.culture import CultureArtifact, CultureProjector
from ..society.impressions import Impression
from .pipeline import MemoryRecord


@dataclass(frozen=True)
class CalibrationCandidate:
    kind: str
    target_id: str
    reason_codes: tuple[str, ...]
    evidence_event_ids: tuple[str, ...]


@dataclass(frozen=True)
class ConsolidationReport:
    duplicate_groups: tuple[tuple[str, ...], ...]
    conflicting_memory_ids: tuple[str, ...]
    calibration_candidates: tuple[CalibrationCandidate, ...]
    direct_policy_changes: tuple[str, ...]
    generated_at: int


class MemoryConsolidator:
    def consolidate(
        self,
        records: tuple[MemoryRecord, ...],
        *,
        now: int,
        impressions: tuple[Impression, ...] = (),
        culture_artifacts: tuple[CultureArtifact, ...] = (),
        completed_loop_ids: tuple[str, ...] = (),
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
        calibration = []
        for impression in impressions:
            if (
                impression.status != "tombstoned"
                and impression.expires_at is not None
                and impression.expires_at <= int(now)
            ):
                calibration.append(
                    CalibrationCandidate(
                        "expire_impression",
                        impression.impression_id,
                        ("impression_expired",),
                        impression.evidence_event_ids,
                    )
                )
        culture_projector = CultureProjector()
        for artifact in culture_artifacts:
            if culture_projector.decay(artifact, now=now) != artifact:
                calibration.append(
                    CalibrationCandidate(
                        "decay_culture",
                        artifact.artifact_id,
                        ("culture_evidence_stale",),
                        artifact.evidence_event_ids,
                    )
                )
        for loop_id in dict.fromkeys(completed_loop_ids):
            if str(loop_id).strip():
                calibration.append(
                    CalibrationCandidate(
                        "close_loop",
                        str(loop_id),
                        ("loop_completed",),
                        (),
                    )
                )
        return ConsolidationReport(
            duplicate_groups=duplicates,
            conflicting_memory_ids=conflicts,
            calibration_candidates=tuple(calibration),
            direct_policy_changes=(),
            generated_at=int(now),
        )


__all__ = (
    "CalibrationCandidate",
    "ConsolidationReport",
    "MemoryConsolidator",
)
