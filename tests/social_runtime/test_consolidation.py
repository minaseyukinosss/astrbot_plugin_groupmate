from __future__ import annotations

from groupmate.social_runtime.memory.consolidation import MemoryConsolidator
from groupmate.social_runtime.memory.pipeline import MemoryCandidate, MemoryPipeline


def _record(event_id):
    return MemoryPipeline().decide(
        MemoryCandidate(
            persona_id="aemeath", group_id="g1", subject_id="u1",
            kind="episode", content="一起排查过数据库故障",
            evidence_event_ids=(event_id,), source_authority="user_event",
            sensitivity="normal", confidence=0.9, importance=0.8, occurred_at=100,
        )
    ).record


def test_consolidation_reports_duplicates_without_direct_policy_mutation():
    report = MemoryConsolidator().consolidate((_record("e1"), _record("e2")), now=1000)

    assert len(report.duplicate_groups) == 1
    assert set(report.duplicate_groups[0]) == {_record("e1").memory_id, _record("e2").memory_id}
    assert report.direct_policy_changes == ()
    assert report.calibration_candidates == ()
