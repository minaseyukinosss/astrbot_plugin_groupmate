from __future__ import annotations

from groupmate.social_runtime.memory.consolidation import MemoryConsolidator
from groupmate.social_runtime.memory.pipeline import MemoryCandidate, MemoryPipeline
from groupmate.social_runtime.society.culture import CultureArtifact
from groupmate.social_runtime.society.impressions import Impression


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


def test_consolidation_proposes_expiry_decay_and_loop_closure_only():
    impression = Impression(
        impression_id="impression:1",
        persona_id="aemeath",
        group_id="g1",
        subject_id="u1",
        statement="临时印象",
        evidence_event_ids=("e1",),
        status="candidate",
        expires_at=500,
        use_scope=("aemeath", "g1", "u1"),
        content_hash="hash:1",
    )
    culture = CultureArtifact(
        persona_id="aemeath",
        group_id="g1",
        artifact_id="culture:1",
        status="active",
        evidence_event_ids=("e1", "e2", "e3"),
        last_evidence_at=100,
        confirmed_by_admin=None,
        version=3,
    )

    report = MemoryConsolidator().consolidate(
        (),
        now=100 + 31 * 24 * 60 * 60,
        impressions=(impression,),
        culture_artifacts=(culture,),
        completed_loop_ids=("loop:1",),
    )

    assert [item.kind for item in report.calibration_candidates] == [
        "expire_impression",
        "decay_culture",
        "close_loop",
    ]
    assert report.direct_policy_changes == ()
