from __future__ import annotations

from groupmate.social_runtime.memory.pipeline import (
    MemoryCandidate,
    MemoryPipeline,
)


def _candidate(**overrides):
    values = {
        "persona_id": "aemeath",
        "group_id": "g1",
        "subject_id": "u1",
        "kind": "user_fact",
        "content": "小夏喜欢喝咖啡",
        "evidence_event_ids": ("e1",),
        "source_authority": "user_event",
        "sensitivity": "normal",
        "confidence": 0.9,
        "importance": 0.7,
        "occurred_at": 100,
    }
    values.update(overrides)
    return MemoryCandidate(**values)


def test_bot_generated_reply_cannot_prove_user_fact():
    decision = MemoryPipeline().decide(
        _candidate(source_authority="bot_output", evidence_event_ids=("bot:m1",))
    )

    assert decision.outcome == "REJECT"
    assert "bot_output_not_user_evidence" in decision.reason_codes


def test_sensitive_candidate_defaults_to_review():
    decision = MemoryPipeline().decide(_candidate(sensitivity="sensitive"))

    assert decision.outcome == "REVIEW"
    assert decision.record is None


def test_conflicting_facts_coexist_and_are_marked():
    pipeline = MemoryPipeline()
    first = pipeline.decide(_candidate()).record
    second = pipeline.decide(
        _candidate(content="小夏不喝咖啡", evidence_event_ids=("e2",)),
        existing=(first,),
    )

    assert second.outcome == "ACCEPT"
    assert second.record.conflict is True
    assert second.conflicting_memory_ids == (first.memory_id,)


def test_unrelated_facts_about_same_subject_are_not_false_conflicts():
    pipeline = MemoryPipeline()
    first = pipeline.decide(_candidate()).record

    second = pipeline.decide(
        _candidate(content="小夏住在上海", evidence_event_ids=("e2",)),
        existing=(first,),
    )

    assert second.outcome == "ACCEPT"
    assert second.record.conflict is False
    assert second.conflicting_memory_ids == ()


def test_low_importance_candidate_is_reviewed_before_persistence():
    decision = MemoryPipeline().decide(_candidate(importance=0.1))

    assert decision.outcome == "REVIEW"
    assert decision.reason_codes == ("below_persistence_threshold",)
    assert decision.record is None


def test_tombstone_blocks_equivalent_text_from_reappearing():
    pipeline = MemoryPipeline()
    candidate = _candidate(content="  小夏喜欢喝咖啡  ")
    content_hash = pipeline.content_hash(candidate)

    decision = pipeline.decide(candidate, tombstone_hashes=(content_hash,))

    assert decision.outcome == "REJECT"
    assert decision.reason_codes == ("tombstone_blocked",)
