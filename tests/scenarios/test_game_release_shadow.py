from __future__ import annotations

import asyncio
from dataclasses import replace

from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.settings import SocialRuntimeSettings
from groupmate.social_runtime.contracts import RuntimeMode, SocialEventEnvelope
from groupmate.social_runtime.knowledge.contracts import (
    NegativeSearchSnapshot,
    VersionSlot,
)
from groupmate.social_runtime.knowledge.repository import KnowledgeRepository
from groupmate.social_runtime.knowledge.resolver import KnowledgeEntityResolver
from groupmate.social_runtime.knowledge.seeds import SeedImporter, load_bundled_seeds
from groupmate.social_runtime.manager import ShadowEvaluation, SocialRuntimeManager


_NOW = 10_000
_GAME_ID = "game:wuthering-waves"


class _ObserveOnlyWorker:
    name = "ambient_social_assessor"

    async def observe(self, _frame, _context):
        return ()


def _event(case: str, text: str) -> SocialEventEnvelope:
    return SocialEventEnvelope.create(
        event_id=f"qq:release:{case}",
        event_type="platform.message",
        occurred_at=_NOW,
        received_at=_NOW,
        persona_id="persona:1",
        group_id="g1",
        actor_id="member:1",
        source_message_id=f"release:{case}",
        correlation_id=f"release:{case}",
        causation_id=None,
        payload={"text": text, "direct_address": False},
    )


def _slot(
    *,
    official_state: str,
    release_state: str = "future",
    rumor_state: str = "none_observed",
    status: str = "active",
    fresh_until: int = _NOW + 500,
    release_at: int | None = None,
    revision: int = 1,
) -> VersionSlot:
    checked_at = _NOW - 20
    released = official_state == "released"
    return VersionSlot.create(
        version_slot_id=f"slot:waves:{release_state}:{official_state}:{rumor_state}",
        game_entity_id=_GAME_ID,
        official_label="6.0" if official_state != "none" else None,
        region="global",
        platform="all",
        release_state=release_state,
        official_state=official_state,
        rumor_state=rumor_state,
        announced_at=checked_at - 100 if official_state != "none" else None,
        release_at=(checked_at - 50 if released else release_at),
        effective_until=None,
        release_checked_at=checked_at if released else None,
        official_checked_at=checked_at if official_state != "none" else None,
        rumor_checked_at=checked_at if rumor_state != "none_observed" else None,
        fresh_until=fresh_until,
        status=status,
        revision=revision,
    )


def _record_probe(
    repository: KnowledgeRepository,
    *,
    status: str,
    unsafe_domain: bool = False,
    diagnostic_code: str | None = None,
) -> None:
    seed = next(
        item for item in load_bundled_seeds() if item.game.entity_id == _GAME_ID
    )
    sources = [dict(item) for item in seed.manifest["official_sources"]]
    if unsafe_domain:
        sources[0]["domain"] = "https://evil.invalid/?query=secret"
    job = repository.enqueue_knowledge_job(
        idempotency_key=f"official_daily_probe:{_GAME_ID}:global:all:{status}",
        job_kind="official_daily_probe",
        entity_id=_GAME_ID,
        request={
            "game_entity_id": _GAME_ID,
            "region": "global",
            "platform": "all",
            "date_or_boundary": status,
            "sources": sources,
        },
        next_attempt_at=_NOW - 20,
        now=_NOW - 20,
    )
    claimed = repository.claim_due_knowledge_job(_NOW - 20)
    assert claimed is not None and claimed.job_id == job.job_id
    if status == "complete":
        repository.complete_knowledge_job(job.job_id, _NOW - 10)
    else:
        repository.retry_knowledge_job(
            job.job_id,
            next_attempt_at=_NOW + 300,
            diagnostic_code=diagnostic_code or f"official_probe_{status}",
            now=_NOW - 10,
        )


def _prepare_case(repository: KnowledgeRepository, case: str) -> None:
    if case == "current":
        repository.save_release_state(
            _slot(official_state="released", release_state="current"), 0
        )
        _record_probe(repository, status="complete")
    elif case == "negative":
        repository.save_release_state(_slot(official_state="none"), 0)
        seed = next(
            item
            for item in load_bundled_seeds()
            if item.game.entity_id == _GAME_ID
        )
        source_ids = tuple(item.source_id for item in seed.official_sources)
        repository.save_negative_snapshot(
            NegativeSearchSnapshot.create(
                snapshot_id="negative:waves:next",
                game_entity_id=_GAME_ID,
                query_intent="verify_version_state",
                probe_status="complete",
                covered_source_ids=source_ids,
                required_source_ids=tuple(
                    item.source_id for item in seed.official_sources if item.required
                ),
                region="global",
                platform="all",
                checked_at=_NOW - 10,
                expires_at=_NOW + 300,
                version_state_revision=1,
            )
        )
    elif case == "preview":
        repository.save_release_state(_slot(official_state="preview"), 0)
        _record_probe(repository, status="complete")
    elif case == "select_next":
        repository.save_release_state(
            _slot(official_state="released", release_state="current"), 0
        )
        repository.save_release_state(
            _slot(official_state="preview", revision=2), 1
        )
        _record_probe(repository, status="complete")
    elif case == "rumor_not_probed":
        _record_probe(repository, status="complete")
    elif case == "coexisting_tracks":
        repository.save_release_state(
            _slot(official_state="preview", rumor_state="corroborated"), 0
        )
        _record_probe(repository, status="complete")
    elif case == "empty_complete":
        _record_probe(repository, status="complete")
    elif case in {"partial", "timed_out"}:
        repository.save_release_state(_slot(official_state="preview"), 0)
        _record_probe(
            repository,
            status=case,
            unsafe_domain=case == "partial",
        )
    elif case == "recovered":
        _record_probe(
            repository,
            status="failed",
            diagnostic_code="knowledge_job_recovered",
        )
    elif case == "unsafe_diagnostic":
        _record_probe(
            repository,
            status="failed",
            diagnostic_code="provider secret exception",
        )
    elif case == "boundary":
        repository.save_release_state(
            _slot(official_state="preview", release_at=_NOW), 0
        )
        _record_probe(repository, status="complete")
    elif case == "stale":
        repository.save_release_state(
            _slot(official_state="preview", fresh_until=_NOW - 1), 0
        )
        _record_probe(repository, status="complete")
    elif case == "disputed":
        repository.save_release_state(
            _slot(
                official_state="preview",
                rumor_state="conflicted",
                status="disputed",
            ),
            0,
        )


def _evaluate(repository: KnowledgeRepository, case: str, text: str):
    async def scenario():
        worker = _ObserveOnlyWorker()
        manager = SocialRuntimeManager(
            database_path=repository.path,
            persona_id="persona:1",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("g1",),
            cognition_workers={worker.name: worker},
            knowledge_resolver=KnowledgeEntityResolver(repository),
            clock=lambda: _NOW,
        )
        await manager.start()
        await manager.ingest(_event(case, text))
        evaluation = (await manager.drain(now=_NOW + 2))[0]
        await manager.close()
        return evaluation

    return asyncio.run(scenario())


def test_shadow_release_scenarios_are_auditable_without_fact_replies(tmp_path):
    cases = (
        ("current", "鸣潮这期版本怎么样", "official_complete", "released", "none_observed"),
        ("negative", "鸣潮下版本有消息吗", "negative_snapshot_valid", "none", "none_observed"),
        ("preview", "鸣潮下版本前瞻呢", "official_complete", "preview", "none_observed"),
        ("select_next", "鸣潮下版本前瞻呢", "official_complete", "preview", "none_observed"),
        ("rumor_not_probed", "鸣潮新版本有爆料吗", "rumor_not_probed", None, None),
        ("coexisting_tracks", "鸣潮新版本爆料呢", "rumor_observed", "preview", "corroborated"),
        ("empty_complete", "鸣潮新版本有消息吗", "official_complete", None, None),
        ("partial", "鸣潮新版本有消息吗", "official_partial", "preview", "none_observed"),
        ("timed_out", "鸣潮新版本有消息吗", "official_timed_out", "preview", "none_observed"),
        ("recovered", "鸣潮新版本有消息吗", "official_failed", None, None),
        ("unsafe_diagnostic", "鸣潮新版本有消息吗", "official_failed", None, None),
        ("boundary", "鸣潮新版本是不是上线了", "knowledge_stale", "preview", "none_observed"),
        ("stale", "鸣潮新版本有消息吗", "knowledge_stale", "preview", "none_observed"),
        ("disputed", "鸣潮新版本到底什么情况", "evidence_disputed", "preview", "conflicted"),
    )

    for case, text, expected_status, official_state, rumor_state in cases:
        repository = KnowledgeRepository(tmp_path / case / "runtime.db")
        SeedImporter(repository, clock=lambda: _NOW - 100).import_all(
            load_bundled_seeds()
        )
        _prepare_case(repository, case)
        evaluation = _evaluate(repository, case, text)
        bridge = AstrBotSocialRuntimeBridge(
            object(),
            SocialRuntimeSettings.from_mapping(
                {
                    "enabled_groups": ["g1"],
                    "runtime_mode": "SHADOW",
                    "generation_provider": "provider:test",
                    "profile_enabled": False,
                }
            ),
            tmp_path / case,
        )
        bridge._knowledge_repository = repository

        enriched = bridge._attach_shadow_knowledge_diagnostic(
            evaluation, now=_NOW + 2
        )
        diagnostic = enriched.knowledge_diagnostic

        assert diagnostic is not None
        assert diagnostic["status"] == expected_status
        assert diagnostic["tracks"]["official"] == official_state
        assert diagnostic["tracks"]["rumor"] == rumor_state
        seed_domains = list(
            dict.fromkeys(
                source.domain
                for seed in load_bundled_seeds()
                if seed.game.entity_id == _GAME_ID
                for source in seed.official_sources
            )
        )
        assert diagnostic["source_domains"] == seed_domains
        assert enriched.candidates == ()
        assert enriched.governor_result.selected_intention_ids == ()
        if case == "empty_complete":
            assert diagnostic["status"] != "negative_snapshot_valid"
        if case == "rumor_not_probed":
            assert diagnostic["probe_status"] == "not_requested"
        if case == "recovered":
            assert diagnostic["probe_reason"] == "knowledge_job_recovered"
        if case == "unsafe_diagnostic":
            assert diagnostic["probe_reason"] == "official_probe_failed"

        restored = ShadowEvaluation.from_capture_evidence(
            replace(enriched).to_capture_evidence()
        )
        assert restored.knowledge_diagnostic == diagnostic
