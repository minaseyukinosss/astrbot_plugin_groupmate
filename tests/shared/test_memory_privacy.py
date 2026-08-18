from __future__ import annotations

from groupmate.social_runtime.memory.pipeline import MemoryCandidate, MemoryPipeline
from groupmate.social_runtime.memory.retrieval import MemoryQuery, MemoryRetriever


def test_sensitive_memory_is_removed_when_query_scope_does_not_allow_it():
    record = MemoryPipeline().decide(
        MemoryCandidate(
            persona_id="aemeath", group_id="g1", subject_id="u1",
            kind="user_fact", content="敏感健康信息", evidence_event_ids=("e1",),
            source_authority="admin_confirmed", sensitivity="sensitive",
            confidence=1.0, importance=1.0, occurred_at=100,
        )
    ).record

    block = MemoryRetriever().retrieve(
        (record,),
        MemoryQuery("aemeath", "g1", "u1", "健康", 200, 100, ("normal",)),
    )

    assert block.items == ()
