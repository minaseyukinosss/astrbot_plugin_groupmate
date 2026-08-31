from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.settings import SocialRuntimeSettings
from groupmate.social_runtime.knowledge.contracts import (
    KnowledgeNeed,
    TopicUnderstandingFrame,
)
from groupmate.social_runtime.knowledge.enrichment import (
    EnrichmentResult,
    SceneGuard,
    SceneGuardCheck,
)

from tests.scenarios.test_parallel_topic_governance import (
    _continuation_evaluation,
)


def _evaluation(group_id: str, scene_version: int):
    return SimpleNamespace(
        scene_version=scene_version,
        frame=SimpleNamespace(trigger_kind="FAST"),
        source_event=SimpleNamespace(group_id=group_id),
    )


def test_group_reply_lanes_search_unlocked_parallel_and_cleanup(tmp_path):
    bridge = AstrBotSocialRuntimeBridge(
        object(), SocialRuntimeSettings.from_mapping({}), tmp_path
    )
    bridge._manager = object()
    assert hasattr(bridge, "_reply_locks")
    assert hasattr(bridge, "_prepare_knowledge_evaluation")
    assert hasattr(bridge, "_revalidate_knowledge_evaluation")
    assert hasattr(bridge, "_handle_evaluations_locked")
    g1_started = asyncio.Event()
    release_g1 = asyncio.Event()
    g2_processed = asyncio.Event()
    process_order: list[tuple[str, int]] = []

    async def prepare(evaluation):
        group_id = evaluation.source_event.group_id
        lock = bridge._reply_locks.get(group_id)
        assert lock is None or not lock.locked()
        if group_id == "g1" and evaluation.scene_version == 1:
            g1_started.set()
            await release_g1.wait()
        return evaluation

    async def revalidate(evaluation, *, inside_lock):
        del inside_lock
        return evaluation

    async def handle_locked(evaluations):
        evaluation = evaluations[0]
        group_id = evaluation.source_event.group_id
        assert bridge._reply_locks[group_id].locked()
        process_order.append((group_id, evaluation.scene_version))
        if group_id == "g2":
            g2_processed.set()

    bridge._prepare_knowledge_evaluation = prepare
    bridge._revalidate_knowledge_evaluation = revalidate
    bridge._handle_evaluations_locked = handle_locked

    async def scenario():
        first = asyncio.create_task(
            bridge._handle_evaluations((_evaluation("g1", 1),))
        )
        await g1_started.wait()
        other_group = asyncio.create_task(
            bridge._handle_evaluations((_evaluation("g2", 1),))
        )
        same_group = asyncio.create_task(
            bridge._handle_evaluations((_evaluation("g1", 2),))
        )
        await asyncio.wait_for(g2_processed.wait(), timeout=0.2)
        assert not first.done()
        release_g1.set()
        await asyncio.gather(first, other_group, same_group)

    asyncio.run(scenario())

    assert process_order == [("g2", 1), ("g1", 1), ("g1", 2)]
    assert bridge._reply_locks == {}


def test_expired_knowledge_snapshot_never_enters_reply_pipeline(tmp_path):
    bridge = AstrBotSocialRuntimeBridge(
        object(),
        SocialRuntimeSettings.from_mapping({}),
        tmp_path,
        clock=lambda: 100,
    )
    bridge._manager = object()
    assert hasattr(bridge, "_prepare_knowledge_evaluation")
    assert hasattr(bridge, "_handle_evaluations_locked")
    assert hasattr(bridge, "_record_stale_knowledge_evaluation")
    entered: list[object] = []
    stale_codes: list[str] = []
    evaluation = _evaluation("g1", 1)
    evaluation.knowledge_snapshot = SimpleNamespace(expires_at=99)

    async def prepare(value):
        return value

    async def handle_locked(evaluations):
        entered.extend(evaluations)

    def record_stale(value, diagnostic_code):
        del value
        stale_codes.append(diagnostic_code)

    bridge._prepare_knowledge_evaluation = prepare
    bridge._handle_evaluations_locked = handle_locked
    bridge._record_stale_knowledge_evaluation = record_stale

    asyncio.run(bridge._handle_evaluations((evaluation,)))

    assert entered == []
    assert stale_codes == ["knowledge_snapshot_expired"]


def test_only_fresh_act_is_enriched_and_attaches_current_guard(tmp_path):
    bridge = AstrBotSocialRuntimeBridge(
        object(),
        SocialRuntimeSettings.from_mapping({}),
        tmp_path,
        clock=lambda: 100,
    )
    calls: list[object] = []
    guard = SceneGuard.create(
        group_id="g1",
        scene_version=3,
        target_id="u1",
        lease_id=None,
        lease_expires_at=None,
        intention_id="continue:1",
        intention_expires_at=130,
    )

    class Manager:
        async def freeze_scene_guard(self, group_id, evaluation, *, now):
            assert (group_id, now) == ("g1", 100)
            return guard

        async def current_scene_guard(self, group_id, evaluation):
            return SceneGuardCheck.valid()

    class Retriever:
        def retrieve(self, frame, group_id, now):
            return ()

    class Assessor:
        def assess(self, frame, hits, *, now):
            return KnowledgeNeed.create(
                outcome="fresh_evidence_required",
                gap_codes=("risk:rumor_status",),
                entity_ids=frame.game_ids,
                query_intents=("verify_rumor_status",),
                expires_at=130,
            )

    class Coordinator:
        async def enrich(self, request):
            lock = bridge._reply_locks.get("g1")
            assert lock is None or not lock.locked()
            calls.append(request)
            return EnrichmentResult(
                status="complete",
                diagnostic_code=None,
                knowledge_committed=True,
                reply_still_valid=True,
                cache_hit=False,
                snapshot=SimpleNamespace(expires_at=120),
                source_domains=("official.example.com",),
                intent_hash="a" * 64,
            )

    frame = TopicUnderstandingFrame.create(
        frame_id="knowledge-frame:1",
        game_ids=("game:wuthering-waves",),
        resolved_entities=(),
        resolved_terms=(),
        discourse_referents=(),
        version_reference=None,
        conversation_intent_hint="ask_version",
        ambiguity_codes=("risk:rumor_status",),
        confidence=1.0,
        supporting_knowledge_ids=(),
    )
    evaluation = _continuation_evaluation()
    evaluation = replace(
        evaluation,
        participation_lane="DIRECT_FAST",
        frame=replace(evaluation.frame, trigger_kind="FAST"),
        topic_understanding=frame,
    )
    bridge._manager = Manager()
    bridge._knowledge_retriever = Retriever()
    bridge._knowledge_need_assessor = Assessor()
    bridge._knowledge_enrichment = Coordinator()

    prepared = asyncio.run(bridge._prepare_knowledge_evaluation(evaluation))

    assert len(calls) == 1
    request = calls[0]
    assert request.lane.value == "DIRECT"
    assert request.need.query_intents == ("rumor_next_version",)
    assert request.hard_deadline - request.requested_at <= 5
    assert prepared.knowledge_scene_guard == guard
    assert prepared.knowledge_snapshot.expires_at == 120

    no_need = replace(evaluation, topic_understanding=None)
    asyncio.run(bridge._prepare_knowledge_evaluation(no_need))
    assert len(calls) == 1
