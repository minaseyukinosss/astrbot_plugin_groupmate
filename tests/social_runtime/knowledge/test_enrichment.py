from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from groupmate.social_runtime.knowledge.contracts import (
    KnowledgeNeed,
    SourceEvidence,
    TopicUnderstandingFrame,
)
from groupmate.social_runtime.knowledge.enrichment import (
    EnrichmentRequest,
    KnowledgeEnrichmentCoordinator,
    SceneGuard,
    SceneGuardCheck,
)
from groupmate.social_runtime.knowledge.repository import KnowledgeRepository
from groupmate.social_runtime.knowledge.search import (
    DiscoverySearchResult,
    SearchRequest,
)
from groupmate.social_runtime.knowledge.sources import (
    OfficialProbeRequest,
    OfficialProbeResult,
    OfficialSourceDefinition,
)
from groupmate.social_runtime.persistence.schema import connect_database


def _evidence(source_id: str, *, official: bool, fetched_at: int) -> SourceEvidence:
    slug = source_id.replace(":", "-")
    domain = "official.example.com" if official else "community.example.com"
    return SourceEvidence.create(
        evidence_id=f"evidence:{slug}",
        source_id=source_id,
        canonical_url=f"https://{domain}/news?id={slug}",
        domain=domain,
        publisher="Official Game" if official else "Community News",
        source_class="official" if official else "unofficial",
        title="版本资料",
        published_at=fetched_at - 1,
        fetched_at=fetched_at,
        evidence_excerpt="有界且待准入的版本资料。",
        content_hash=("a" if official else "b") * 64,
    )


class _Probe:
    def __init__(self, now, *, status: str = "complete") -> None:
        self.now = now
        self.status = status
        self.calls = 0
        self.evidence = _evidence(
            "source:official:registry", official=True, fetched_at=now()
        )

    async def probe(self, request):
        self.calls += 1
        if self.status == "complete":
            return OfficialProbeResult.create(
                request=request,
                status="complete",
                evidence=(self.evidence,),
                covered_source_ids=(request.sources[0].source_id,),
            )
        return OfficialProbeResult.create(
            request=request,
            status="partial",
            evidence=(self.evidence,),
            covered_source_ids=(request.sources[0].source_id,),
            diagnostic_code="official_probe_partial",
        )


class _Search:
    def __init__(self, now) -> None:
        self.now = now
        self.calls = 0

    async def search(self, request):
        self.calls += 1
        return DiscoverySearchResult.create(
            request=request,
            status="complete",
            candidates=(
                _evidence("source:rumor:new", official=False, fetched_at=self.now()),
            ),
            completed_at=self.now(),
        )


def _frame(game_id: str = "game:genshin-impact") -> TopicUnderstandingFrame:
    return TopicUnderstandingFrame.create(
        frame_id="frame:1",
        game_ids=(game_id,),
        resolved_entities=(),
        resolved_terms=(),
        discourse_referents=(),
        version_reference=None,
        conversation_intent_hint="ask_version",
        ambiguity_codes=(),
        confidence=1.0,
        supporting_knowledge_ids=(),
    )


def _request(
    now: int,
    *,
    lane: str = "DIRECT",
    rumor: bool = True,
    game_id: str = "game:genshin-impact",
    request_id: str = "request:1",
    hard_after: float = 5,
) -> EnrichmentRequest:
    intents = ("rumor_next_version",) if rumor else ("official_next_version",)
    source = OfficialSourceDefinition.create(
        source_id="source:official:registry",
        game_entity_id=game_id,
        publisher="Official Game",
        canonical_url="https://official.example.com/news",
        required=True,
    )
    official = OfficialProbeRequest.create(
        request_id=f"official:{request_id}",
        game_entity_id=game_id,
        query_intent=intents[0],
        sources=(source,),
        region="cn",
        platform="all",
        requested_at=now,
    )
    search = SearchRequest.create(
        request_id=f"search:{request_id}",
        game_entity_id=game_id,
        game_name="原神",
        entity_id=None,
        entity_name=None,
        query_intents=intents,
        region="cn",
        platform="all",
        max_results=2,
        deadline=max(int(now) + 1, int(now + hard_after)),
        entity_hint=None,
        now=int(now),
    )
    return EnrichmentRequest.create(
        request_id=request_id,
        lane=lane,
        scene_guard=SceneGuard.create(
            group_id="group:1",
            scene_version=3,
            target_id="member:1",
            lease_id="lease:1" if lane == "CONTINUATION" else None,
            lease_expires_at=now + 30 if lane == "CONTINUATION" else None,
            intention_id="intention:1",
            intention_expires_at=now + 30,
        ),
        frame=_frame(game_id),
        need=KnowledgeNeed.create(
            outcome="fresh_evidence_required",
            gap_codes=("version_state_stale",),
            entity_ids=(game_id,),
            query_intents=intents,
            expires_at=now + 30,
        ),
        official_request=official,
        search_request=search,
        requested_at=now,
        soft_deadline=now + min(3, hard_after),
        hard_deadline=now + hard_after,
        version_slot_id=None,
        version_state_revision=0,
        region="cn",
        platform="all",
    )


def test_lanes_singleflight_success_cache_and_partial_results(tmp_path):
    now = [int(datetime(2026, 8, 31, 12, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())]
    repository = KnowledgeRepository(tmp_path / "groupmate-social-runtime-v2.db")
    repository.upsert_entity(
        entity_id="game:genshin-impact",
        entity_type="game",
        canonical_name="原神",
        canonical_game_id="game:genshin-impact",
        status="active",
        now=now[0] - 1,
    )
    with connect_database(repository.path) as db:
        db.execute(
            "INSERT INTO negative_search_snapshots(snapshot_id,game_entity_id,"
            "query_intent,region,platform,covered_source_ids_json,checked_at,"
            "expires_at,version_state_revision,status,diagnostic_code) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                "negative:old",
                "game:genshin-impact",
                "rumor_next_version",
                "cn",
                "all",
                '{"covered":[],"required":[]}',
                now[0] - 10,
                now[0] + 600,
                1,
                "active",
                "official_no_matching_update",
            ),
        )
    probe = _Probe(lambda: now[0])
    search = _Search(lambda: now[0])
    coordinator = KnowledgeEnrichmentCoordinator(
        repository,
        official_probe=probe,
        discovery_search=search,
        scene_guard_validator=lambda guard, checked_at: SceneGuardCheck.valid(),
        clock=lambda: now[0],
    )

    async def exercise():
        first, second = await asyncio.gather(
            coordinator.enrich(_request(now[0], request_id="one")),
            coordinator.enrich(_request(now[0], request_id="two")),
        )
        now[0] += 599
        cached = await coordinator.enrich(_request(now[0], request_id="three"))
        ambient = await coordinator.enrich(
            _request(now[0], lane="AMBIENT", request_id="ambient")
        )
        return first, second, cached, ambient

    first, second, cached, ambient = asyncio.run(exercise())
    assert probe.calls == search.calls == 1
    assert all(item.knowledge_committed for item in (first, second, cached))
    assert all(item.reply_still_valid for item in (first, second, cached))
    assert not first.cache_hit and not second.cache_hit and cached.cache_hit
    assert ambient.diagnostic_code == "ambient_search_disabled"
    assert not ambient.knowledge_committed and not ambient.reply_still_valid
    with connect_database(repository.path) as db:
        negative = db.execute(
            "SELECT status,diagnostic_code FROM negative_search_snapshots "
            "WHERE snapshot_id='negative:old'"
        ).fetchone()
    assert tuple(negative) == ("invalidated", "new_official_evidence")

    partial_probe = _Probe(lambda: now[0], status="partial")
    partial = KnowledgeEnrichmentCoordinator(
        repository,
        official_probe=partial_probe,
        discovery_search=search,
        scene_guard_validator=lambda guard, checked_at: SceneGuardCheck.valid(),
        clock=lambda: now[0],
    )

    async def partial_twice():
        await partial.enrich(
            _request(now[0], rumor=False, game_id="game:delta-force", request_id="p1")
        )
        await partial.enrich(
            _request(now[0], rumor=False, game_id="game:delta-force", request_id="p2")
        )

    asyncio.run(partial_twice())
    assert partial_probe.calls == 2


def test_atomic_shanghai_quota_queue_deadline_and_safe_usage(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    repository = KnowledgeRepository(path)
    before_midnight = int(
        datetime(2026, 8, 31, 23, 59, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
    )
    first = repository.reserve_provider_quota(
        intent_hash="a" * 64, now=before_midnight, hourly_limit=1, daily_limit=1
    )
    assert first is not None
    assert repository.reserve_provider_quota(
        intent_hash="b" * 64, now=before_midnight, hourly_limit=1, daily_limit=1
    ) is None
    repository.release_provider_quota(first)
    replacement = repository.reserve_provider_quota(
        intent_hash="c" * 64, now=before_midnight, hourly_limit=1, daily_limit=1
    )
    assert replacement is not None
    repository.finish_provider_quota(
        replacement,
        source_domains=("official.example.com",),
        latency_ms=8,
        result_kind="provider_complete",
        diagnostic_code=None,
    )
    after_midnight = before_midnight + 120
    assert repository.reserve_provider_quota(
        intent_hash="d" * 64, now=after_midnight, hourly_limit=1, daily_limit=1
    ) is not None

    class BlockingProbe(_Probe):
        def __init__(self) -> None:
            super().__init__(lambda: int(time.time()))
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def probe(self, request):
            self.calls += 1
            self.started.set()
            await self.release.wait()
            return OfficialProbeResult.create(
                request=request,
                status="complete",
                evidence=(self.evidence,),
                covered_source_ids=(request.sources[0].source_id,),
            )

    blocking = BlockingProbe()
    live = KnowledgeEnrichmentCoordinator(
        repository,
        official_probe=blocking,
        discovery_search=_Search(lambda: int(time.time())),
        scene_guard_validator=lambda guard, checked_at: SceneGuardCheck.valid(),
        clock=time.time,
        max_provider_concurrency=1,
    )

    async def queue_timeout():
        current = time.time()
        leader = asyncio.create_task(
            live.enrich(_request(current, rumor=False, game_id="game:a", request_id="a"))
        )
        await blocking.started.wait()
        waiter = await live.enrich(
            _request(
                time.time(),
                rumor=False,
                game_id="game:b",
                request_id="b",
                hard_after=0.05,
            )
        )
        blocking.release.set()
        await leader
        return waiter

    waiter = asyncio.run(queue_timeout())
    assert waiter.diagnostic_code == "enrichment_deadline_exceeded"
    assert blocking.calls == 1
    with connect_database(path) as db:
        rows = db.execute(
            "SELECT query_intent_hash,source_domains_json,cache_hit,result_kind,"
            "diagnostic_code FROM knowledge_usage"
        ).fetchall()
    assert rows
    assert all(row[0] is None or len(row[0]) == 64 for row in rows)
    assert all(isinstance(json.loads(row[1]), list) for row in rows)
    assert not any("原神" in "|".join(str(value or "") for value in row) for row in rows)
