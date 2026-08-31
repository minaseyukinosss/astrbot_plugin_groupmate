from __future__ import annotations

import importlib

import pytest

from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.knowledge.repository import KnowledgeRepository
from groupmate.social_runtime.knowledge.seeds import (
    SeedImporter,
    load_bundled_seeds,
)


def _resolver_module():
    return importlib.import_module("groupmate.social_runtime.knowledge.resolver")


def _retrieval_module():
    return importlib.import_module("groupmate.social_runtime.knowledge.retrieval")


def _runtime(tmp_path):
    repository = KnowledgeRepository(tmp_path / "knowledge.db")
    SeedImporter(repository, clock=lambda: 10).import_all(load_bundled_seeds())
    resolver = _resolver_module().KnowledgeEntityResolver(repository)
    retriever = _retrieval_module().KnowledgeRetriever(repository)
    assessor = _retrieval_module().KnowledgeNeedAssessor()
    return resolver, retriever, assessor


def _event(event_id: str, text: str, *, direct=False):
    return SocialEventEnvelope.create(
        event_id=event_id,
        event_type="platform.message",
        occurred_at=100,
        received_at=101,
        persona_id="persona:1",
        group_id="g1",
        actor_id="human:1",
        source_message_id=event_id,
        correlation_id=event_id,
        causation_id=None,
        payload={"text": text, "direct_address": direct},
    )


def _resolve(runtime, text, *, direct=False):
    resolver, retriever, assessor = runtime
    frame = resolver.resolve(_event("e", text, direct=direct), (), "g1", now=200)
    hits = retriever.retrieve(frame, "g1", now=200)
    need = assessor.assess(frame, hits, now=200)
    return frame, hits, need


def test_retrieval_is_bounded_and_contains_only_safe_local_summaries(tmp_path):
    runtime = _runtime(tmp_path)
    frame, hits, _need = _resolve(runtime, "鸣潮抽卡保底圣遗物怎么配队")

    assert frame.game_ids == ("game:wuthering-waves",)
    assert 1 <= len(hits) <= 8
    assert all(hit.knowledge_id and hit.safe_summary for hit in hits)
    assert all(hit.evidence_level == "bundled" for hit in hits)
    assert all(not hasattr(hit, "source_excerpt") for hit in hits)


def test_stable_term_chat_is_local_sufficient(tmp_path):
    _frame, hits, need = _resolve(_runtime(tmp_path), "鸣潮保底怎么理解")

    assert hits
    assert need.outcome.value == "local_sufficient"
    assert need.query_intents == ()


def test_unknown_game_requests_background_learning(tmp_path):
    frame, hits, need = _resolve(
        _runtime(tmp_path), "最近在玩碧蓝幻想Relink"
    )

    assert hits == ()
    assert "unknown_game_entity" in frame.ambiguity_codes
    assert need.outcome.value == "background_learning"


@pytest.mark.parametrize(
    "text",
    [
        "鸣潮新版本有什么",
        "鸣潮明天更新吗",
        "鸣潮新角色有哪些",
        "鸣潮这个伤害有多少",
        "鸣潮官方公布了吗",
        "鸣潮最近有什么爆料",
    ],
)
def test_temporal_lists_numbers_and_status_require_fresh_evidence(
    tmp_path, text
):
    _frame, _hits, need = _resolve(_runtime(tmp_path), text)

    assert need.outcome.value == "fresh_evidence_required"
    assert need.query_intents
    assert need.expires_at == 500


def test_direct_version_question_without_unique_game_is_unresolvable(tmp_path):
    frame, _hits, need = _resolve(
        _runtime(tmp_path), "新版本怎么样", direct=True
    )

    assert "ambiguous_game_for_version" in frame.ambiguity_codes
    assert "direct_unresolved" in frame.ambiguity_codes
    assert need.outcome.value == "unresolvable"


def test_ordinary_non_knowledge_chat_needs_no_knowledge_work(tmp_path):
    frame, hits, need = _resolve(_runtime(tmp_path), "今天天气不错")

    assert frame.game_ids == ()
    assert frame.conversation_intent_hint is None
    assert hits == ()
    assert need.outcome.value == "none"
