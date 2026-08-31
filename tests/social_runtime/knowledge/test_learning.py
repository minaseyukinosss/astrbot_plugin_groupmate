from __future__ import annotations

import asyncio

from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.knowledge.contracts import (
    KnowledgeObservation,
    SourceEvidence,
)
from groupmate.social_runtime.knowledge.jobs import KnowledgeJobService
from groupmate.social_runtime.knowledge.observation import KnowledgeObservationService
from groupmate.social_runtime.knowledge.repository import KnowledgeRepository
from groupmate.social_runtime.knowledge.resolver import KnowledgeEntityResolver
from groupmate.social_runtime.knowledge.retrieval import KnowledgeRetriever
from groupmate.social_runtime.knowledge.search import DiscoverySearchResult


NOW = 1_786_000_000


def _event(index: int, *, group_id="g1", actor_id="u1", scene_ref="s1"):
    text = "最近在玩碧蓝幻想Relink，挺有意思"
    return SocialEventEnvelope.create(
        event_id=f"event:{group_id}:{index}",
        event_type="platform.message",
        occurred_at=NOW,
        received_at=NOW,
        persona_id="persona:1",
        group_id=group_id,
        actor_id=actor_id,
        source_message_id=f"message:{group_id}:{index}",
        correlation_id=f"event:{group_id}:{index}",
        causation_id=None,
        payload={
            "text": text,
            "segments": [{"type": "text", "data": {"text": text}}],
            "scene_ref": scene_ref,
            "social_eligible": True,
            "interaction_owner": "UNKNOWN",
        },
    )


def test_unknown_game_learning_is_group_scoped_thresholded_and_idempotent(
    tmp_path,
):
    repository = KnowledgeRepository(tmp_path / "knowledge.db")
    service = KnowledgeObservationService(
        repository=repository,
        group_ids=("g1", "g2"),
        install_salt="learning-test-salt",
        clock=lambda: NOW,
    )

    async def scenario():
        for index in range(20):
            await service.observe(_event(index))
        await service.process_pending()
        assert [
            job
            for job in repository.knowledge_jobs()
            if job.job_kind == "unknown_entity_learning"
        ] == []

        for index, actor, scene in (
            (20, "u2", "s2"),
            (21, "u3", "s3"),
            (22, "u3", "s4"),
        ):
            await service.observe(_event(index, actor_id=actor, scene_ref=scene))
        await service.observe(
            _event(1, group_id="g2", actor_id="u9", scene_ref="x1")
        )
        await service.process_pending()

    asyncio.run(scenario())

    jobs = [
        job
        for job in repository.knowledge_jobs()
        if job.job_kind == "unknown_entity_learning"
    ]
    assert len(jobs) == 1
    assert jobs[0].group_id == "g1"
    assert jobs[0].request == {
        "entity_hint": "碧蓝幻想Relink",
        "observation_id": "knowledge-observation:a52a2263b57bf39212eac774a4309de4",
        "window_seconds": 604800,
    }


def _source(source_id: str, domain: str, suffix: str) -> SourceEvidence:
    return SourceEvidence.create(
        evidence_id=f"evidence:{source_id}",
        source_id=f"source:{source_id}",
        canonical_url=f"https://{domain}/games/relink",
        domain=domain,
        publisher=domain,
        source_class="secondary",
        title="碧蓝幻想Relink 游戏介绍",
        published_at=NOW - 100,
        fetched_at=NOW,
        evidence_excerpt="碧蓝幻想Relink是一款动作角色扮演游戏。",
        content_hash=suffix * 64,
    )


class _Discovery:
    def __init__(self, candidates):
        self.candidates = tuple(candidates)
        self.requests = []

    async def search(self, request):
        self.requests.append(request)
        return DiscoverySearchResult.create(
            request=request,
            status="complete",
            candidates=self.candidates,
            completed_at=NOW,
        )


class _UnusedOfficialProbe:
    async def probe(self, request):
        raise AssertionError(
            f"official probe must not run for {request.request_id}"
        )


def test_learning_requires_corroboration_then_activates_only_stable_semantics(
    tmp_path,
):
    repository = KnowledgeRepository(tmp_path / "knowledge.db")
    game_id = repository.ensure_candidate_game("碧蓝幻想Relink", now=NOW)
    repository.enqueue_knowledge_job(
        idempotency_key=f"unknown_entity_learning:g1:{game_id}",
        job_kind="unknown_entity_learning",
        group_id="g1",
        entity_id=game_id,
        request={
            "entity_hint": "碧蓝幻想Relink",
            "observation_id": "observation:1",
            "window_seconds": 604800,
        },
        next_attempt_at=NOW,
        now=NOW,
    )
    first = _source("one", "one.example.com", "a")
    second = _source("two", "two.example.net", "b")
    discovery = _Discovery((first,))
    clock = [NOW]
    service = KnowledgeJobService(
        repository,
        probe=_UnusedOfficialProbe(),
        discovery_search=discovery,
        clock=lambda: clock[0],
    )
    first_claim = repository.claim_due_knowledge_job(NOW)
    assert first_claim is not None

    asyncio.run(service._run_job(first_claim))

    retry = repository.knowledge_jobs()[0]
    assert retry.status == "retry"
    assert retry.diagnostic_code == "learning_evidence_insufficient"
    assert repository.entities((game_id,))[0].status == "candidate"

    discovery.candidates = (first, second)
    clock[0] = retry.next_attempt_at
    second_claim = repository.claim_due_knowledge_job(clock[0])
    assert second_claim is not None
    asyncio.run(service._run_job(second_claim))

    assert repository.knowledge_jobs()[0].status == "completed"
    assert repository.entities((game_id,))[0].status == "active"
    assert discovery.requests[-1].entity_hint == "碧蓝幻想Relink"
    assert repository.game_alias_matches("今天继续玩碧蓝幻想Relink") == (
        (game_id, "碧蓝幻想relink"),
    )
    claims = repository.active_claims((game_id,), limit=8)
    assert [
        (item.claim_kind, item.evidence_level, item.safe_summary)
        for item in claims
    ] == [
        (
            "stable_semantic",
            "corroborated",
            "碧蓝幻想Relink是一款动作角色扮演游戏。",
        )
    ]

    frame = KnowledgeEntityResolver(repository).resolve(
        _event(99), (), "g1", now=NOW
    )
    hits = KnowledgeRetriever(repository).retrieve(frame, "g1", NOW)
    assert frame.game_ids == (game_id,)
    assert [item.safe_summary for item in hits] == [
        "碧蓝幻想Relink是一款动作角色扮演游戏。"
    ]

    repository.enqueue_knowledge_job(
        idempotency_key=f"unknown_entity_learning:g2:{game_id}",
        job_kind="unknown_entity_learning",
        group_id="g2",
        entity_id=game_id,
        request={
            "entity_hint": "碧蓝幻想Relink",
            "observation_id": "observation:g2",
            "window_seconds": 604800,
        },
        next_attempt_at=clock[0],
        now=clock[0],
    )
    reused_claim = repository.claim_due_knowledge_job(clock[0])
    assert reused_claim is not None
    asyncio.run(service._run_job(reused_claim))

    reused = next(
        item for item in repository.knowledge_jobs() if item.group_id == "g2"
    )
    assert reused.status == "completed"
    assert len(discovery.requests) == 2


def test_retention_trims_old_unlinked_text_in_bounded_batches(tmp_path):
    repository = KnowledgeRepository(tmp_path / "knowledge.db")
    for index, secret in ((1, "secret-one"), (2, "secret-two")):
        repository.append_observation(
            KnowledgeObservation.create(
                observation_id=f"observation:{index}",
                origin_class="human_chat",
                scope_kind="group",
                group_id="g1",
                author_ref=f"author:{index}",
                source_event_id=f"event:{index}",
                source_id=f"scene:{index}",
                entity_hint=None,
                safe_summary=secret,
                content_hash=str(index) * 64,
                occurred_at=index,
                recorded_at=index,
                status="admitted",
            )
        )
    repository.upsert_source(
        SourceEvidence.create(
            evidence_id="evidence:old",
            source_id="source:old",
            canonical_url="https://old.example.com/game",
            domain="old.example.com",
            publisher="Old",
            source_class="secondary",
            title="旧搜索候选",
            published_at=1,
            fetched_at=1,
            evidence_excerpt="secret-search-excerpt",
            content_hash="c" * 64,
        )
    )
    now = 200 * 24 * 60 * 60

    first = repository.apply_knowledge_retention(now=now, batch_size=1)

    storage = repository.knowledge_storage_text()
    assert first == {"observations_trimmed": 1, "sources_trimmed": 0}
    assert "secret-one" not in storage
    assert "secret-two" in storage

    second = repository.apply_knowledge_retention(now=now, batch_size=2)

    assert second == {"observations_trimmed": 1, "sources_trimmed": 1}
    assert "secret-two" not in repository.knowledge_storage_text()
