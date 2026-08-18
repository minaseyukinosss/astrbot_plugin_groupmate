from __future__ import annotations

import pytest

from groupmate.social_runtime.memory.pipeline import MemoryCandidate, MemoryPipeline
from groupmate.social_runtime.memory.retrieval import MemoryQuery, MemoryRetriever
from groupmate.social_runtime.persistence.repositories import ScopeRequiredError


def _record(group_id, content, event_id):
    candidate = MemoryCandidate(
        persona_id="aemeath", group_id=group_id, subject_id="u1",
        kind="user_fact", content=content, evidence_event_ids=(event_id,),
        source_authority="user_event", sensitivity="normal", confidence=0.9,
        importance=0.8, occurred_at=100,
    )
    return MemoryPipeline().decide(candidate).record


def test_retrieval_never_crosses_group_scope():
    records = (
        _record("g1", "小夏喜欢咖啡", "e1"),
        _record("g2", "小夏喜欢奶茶", "e2"),
    )
    query = MemoryQuery(
        persona_id="aemeath", group_id="g1", subject_id="u1",
        text="小夏喜欢什么", now=200, token_budget=100,
        allowed_sensitivities=("normal",),
    )

    block = MemoryRetriever().retrieve(records, query)

    assert [item.summary for item in block.items] == ["小夏喜欢咖啡"]
    assert all(item.group_id == "g1" for item in block.items)


def test_retrieval_requires_scope_and_respects_token_budget():
    retriever = MemoryRetriever()
    with pytest.raises(ScopeRequiredError):
        retriever.retrieve(
            (),
            MemoryQuery("aemeath", "", "u1", "咖啡", 200, 10, ("normal",)),
        )

    block = retriever.retrieve(
        (_record("g1", "非常长的咖啡偏好描述" * 10, "e1"),),
        MemoryQuery("aemeath", "g1", "u1", "咖啡", 200, 4, ("normal",)),
    )
    assert block.tokens_used <= 4
