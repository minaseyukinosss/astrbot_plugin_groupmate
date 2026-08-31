from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from groupmate.social_runtime.knowledge.enrichment import (
    KnowledgeEnrichmentCoordinator,
    SceneGuardCheck,
    normalized_enrichment_key,
)
from groupmate.social_runtime.knowledge.repository import KnowledgeRepository
from groupmate.social_runtime.persistence.schema import connect_database

from tests.social_runtime.knowledge.test_enrichment import _Probe, _Search, _request


def test_scene_expiry_keeps_admission_and_staged_job_recovers_without_reply(tmp_path):
    now = [int(datetime(2026, 8, 31, 14, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())]
    path = tmp_path / "groupmate-social-runtime-v2.db"
    repository = KnowledgeRepository(path)
    probe = _Probe(lambda: now[0])
    stale_scene = KnowledgeEnrichmentCoordinator(
        repository,
        official_probe=probe,
        discovery_search=_Search(lambda: now[0]),
        scene_guard_validator=lambda guard, checked_at: SceneGuardCheck.invalid(
            "scene_version_changed"
        ),
        clock=lambda: now[0],
    )
    result = asyncio.run(
        stale_scene.enrich(_request(now[0], rumor=False, request_id="stale"))
    )
    assert result.knowledge_committed
    assert not result.reply_still_valid
    assert result.diagnostic_code == "scene_version_changed"

    recovery_request = _request(
        now[0], rumor=False, game_id="game:recovery", request_id="before-crash"
    )
    request_hash = normalized_enrichment_key(recovery_request)
    job = repository.acquire_enrichment_job(
        request_hash=request_hash,
        entity_id="game:recovery",
        query_intents=("official_next_version",),
        now=now[0],
    )
    staged = _Probe(lambda: now[0]).evidence
    repository.stage_enrichment_job(
        job.job_id,
        evidence=(staged,),
        official_complete=True,
        discovery_complete=True,
        diagnostic_code=None,
        now=now[0],
    )
    repository.commit_enrichment_evidence(
        game_entity_id="game:recovery", evidence=(staged,)
    )

    now[0] += 6
    assert repository.recover_enrichment_jobs(now[0], stale_before=now[0]) == 1
    no_provider = _Probe(lambda: now[0])
    recovered = KnowledgeEnrichmentCoordinator(
        repository,
        official_probe=no_provider,
        discovery_search=_Search(lambda: now[0]),
        scene_guard_validator=lambda guard, checked_at: SceneGuardCheck.valid(),
        clock=lambda: now[0],
    )
    recovered_result = asyncio.run(
        recovered.enrich(
            _request(
                now[0],
                rumor=False,
                game_id="game:recovery",
                request_id="after-restart",
            )
        )
    )
    assert recovered_result.knowledge_committed
    assert recovered_result.reply_still_valid
    assert no_provider.calls == 0

    with connect_database(path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM knowledge_sources WHERE content_hash=?",
            (staged.content_hash,),
        ).fetchone()[0] == 1
        payload = db.execute(
            "SELECT request_json FROM knowledge_jobs WHERE job_id=?", (job.job_id,)
        ).fetchone()[0]
    assert all(word not in payload for word in ("reply", "target", "lease", "intention", "scene"))
