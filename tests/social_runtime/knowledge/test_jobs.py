from __future__ import annotations

import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from groupmate.social_runtime.knowledge.contracts import SourceClass, SourceEvidence, VersionSlot
from groupmate.social_runtime.knowledge.repository import KnowledgeRepository
from groupmate.social_runtime.knowledge.seeds import SeedImporter, load_bundled_seeds
from groupmate.social_runtime.knowledge.sources import OfficialProbeResult
from groupmate.social_runtime.persistence.schema import connect_database


def _at(day: int, hour: int = 0, minute: int = 30) -> int:
    return int(datetime(2026, 8, day, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())


class _Probe:
    def __init__(self, status: str = "complete") -> None:
        self.status = status

    async def probe(self, request):
        if self.status != "complete":
            return OfficialProbeResult.create(
                request=request,
                status=self.status,
                evidence=(),
                covered_source_ids=(),
                diagnostic_code=f"official_probe_{'timed_out' if self.status == 'timed_out' else self.status if self.status != 'partial' else 'partial'}",
            )
        evidence = tuple(
            SourceEvidence.create(
                evidence_id=f"evidence:{source.source_id}",
                source_id=source.source_id,
                canonical_url=source.canonical_url,
                domain=source.domain,
                publisher=source.publisher,
                source_class=SourceClass.OFFICIAL,
                title="Official entry point",
                published_at=None,
                fetched_at=request.requested_at,
                evidence_excerpt="Official entry point",
                content_hash="a" * 64,
            )
            for source in request.sources
        )
        return OfficialProbeResult.create(
            request=request,
            status="complete",
            evidence=evidence,
            covered_source_ids=tuple(source.source_id for source in request.sources),
            diagnostic_code=None,
        )


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


def _repository(tmp_path, *, all_seeds: bool = False) -> KnowledgeRepository:
    repository = KnowledgeRepository(tmp_path / "groupmate-social-runtime-v2.db")
    seeds = load_bundled_seeds()
    SeedImporter(repository, clock=lambda: _at(27)).import_all(
        seeds if all_seeds else seeds[:1]
    )
    return repository


def _slot(
    game_id: str,
    *,
    slot_id: str | None = None,
    checked_at: int = 1,
    release_at: int | None = None,
    revision: int = 1,
) -> VersionSlot:
    return VersionSlot.create(
        version_slot_id=slot_id or f"slot:{game_id}:preview",
        game_entity_id=game_id,
        official_label="preview",
        region="global",
        platform="all",
        release_state="future",
        official_state="preview",
        rumor_state="none_observed",
        announced_at=checked_at,
        release_at=release_at,
        effective_until=None,
        release_checked_at=None,
        official_checked_at=checked_at,
        rumor_checked_at=None,
        fresh_until=checked_at + 1,
        status="active",
        revision=revision,
    )


def test_daily_jobs_use_shanghai_keyed_jitter_and_survive_restart(tmp_path):
    from groupmate.social_runtime.knowledge.jobs import KnowledgeJobService

    repository = _repository(tmp_path, all_seeds=True)
    now = _at(28)
    service = KnowledgeJobService(repository, probe=_Probe(), clock=lambda: now)
    asyncio.run(service.wake())

    jobs = repository.knowledge_jobs()
    assert len(jobs) == 5
    midnight = _at(28, 0, 0)
    assert {job.request["game_entity_id"] for job in jobs} == {
        "game:delta-force",
        "game:genshin-impact",
        "game:honkai-star-rail",
        "game:wuthering-waves",
        "game:zenless-zone-zero",
    }
    assert all(job.idempotency_key.endswith(":global:all:2026-08-28") for job in jobs)
    assert all(0 <= job.next_attempt_at - midnight <= 20 * 60 for job in jobs)

    restarted = KnowledgeJobService(repository, probe=_Probe(), clock=lambda: now)
    asyncio.run(restarted.wake())
    assert len(repository.knowledge_jobs()) == 5

    for _ in range(5):
        claimed = repository.claim_due_knowledge_job(now)
        assert claimed is not None
        repository.complete_knowledge_job(claimed.job_id, now)
    before_twenty_four_hours = now + (23 * 60 + 50) * 60
    asyncio.run(
        KnowledgeJobService(
            repository, probe=_Probe(), clock=lambda: before_twenty_four_hours
        ).wake()
    )
    assert len(repository.knowledge_jobs()) == 5

    seed = load_bundled_seeds()[0]
    with connect_database(repository.path) as db:
        db.execute(
            "INSERT INTO knowledge_seeds(seed_id,seed_version,content_hash,status,"
            "manifest_json,imported_at) VALUES(?,?,?,?,?,?)",
            (
                "operator:extra",
                1,
                "f" * 64,
                "active",
                json.dumps(
                    {
                        "game": {"entity_id": "game:operator-extra"},
                        "official_sources": [],
                    }
                ),
                now,
            ),
        )
    after_boundary = now + 25 * 60 * 60
    asyncio.run(
        KnowledgeJobService(
            repository, probe=_Probe(), clock=lambda: after_boundary
        ).wake()
    )
    assert "game:operator-extra" not in {
        job.request["game_entity_id"] for job in repository.knowledge_jobs()
    }

    outstanding = next(
        job
        for job in repository.knowledge_jobs()
        if job.idempotency_key.endswith(":2026-08-29")
        and job.entity_id == seed.game.entity_id
    )
    with connect_database(repository.path) as db:
        db.execute(
            "UPDATE knowledge_jobs SET status='retry',attempt=1,"
            "next_attempt_at=?,diagnostic_code='official_probe_failed' "
            "WHERE job_id=?",
            (after_boundary + 60, outstanding.job_id),
        )
    next_day = after_boundary + 24 * 60 * 60
    asyncio.run(
        KnowledgeJobService(repository, probe=_Probe(), clock=lambda: next_day).wake()
    )
    assert len(
        [
            job
            for job in repository.knowledge_jobs()
            if job.job_kind == "official_daily_probe"
            and job.entity_id == seed.game.entity_id
            and job.status != "completed"
        ]
    ) == 1

    repository.save_release_state(
        _slot(seed.game.entity_id, slot_id="slot:boundary", release_at=next_day + 60),
        expected_revision=0,
    )
    asyncio.run(
        KnowledgeJobService(repository, probe=_Probe(), clock=lambda: next_day).wake()
    )
    assert not [
        job
        for job in repository.knowledge_jobs()
        if job.job_kind == "time_boundary_revalidation"
    ]
    asyncio.run(
        KnowledgeJobService(
            repository, probe=_Probe(), clock=lambda: next_day + 60
        ).wake()
    )
    assert len(
        [
            job
            for job in repository.knowledge_jobs()
            if job.job_kind == "time_boundary_revalidation"
        ]
    ) == 1

@pytest.mark.parametrize(
    ("probe_status", "expected_status", "expected_checked"),
    (
        ("complete", "completed", _at(28)),
        ("partial", "retry", 1),
        ("timed_out", "retry", 1),
        ("unavailable", "retry", 1),
        ("failed", "retry", 1),
    ),
)
def test_probe_result_commits_only_complete_refreshes(
    tmp_path, probe_status, expected_status, expected_checked
):
    from groupmate.social_runtime.knowledge.jobs import KnowledgeJobService

    repository = _repository(tmp_path)
    game_id = load_bundled_seeds()[0].game.entity_id
    repository.save_release_state(_slot(game_id), expected_revision=0)
    now = _at(28)
    service = KnowledgeJobService(
        repository, probe=_Probe(probe_status), clock=lambda: now
    )

    async def run():
        await service.start()
        await service.wake()
        await _settle()
        await service.close()

    asyncio.run(run())

    job = repository.knowledge_jobs()[0]
    slot = repository.load_release_state(game_id, "global", "all")[0]
    assert job.status == expected_status
    assert slot.official_checked_at == expected_checked
    if expected_status == "retry":
        assert job.diagnostic_code == f"official_probe_{'timed_out' if probe_status == 'timed_out' else probe_status if probe_status != 'partial' else 'partial'}"
        assert now < job.next_attempt_at <= now + 3600


def test_complete_probe_commits_all_slots_atomically(tmp_path, monkeypatch):
    from groupmate.social_runtime.knowledge.jobs import KnowledgeJobService

    def prepared_repository(path):
        repository = _repository(path)
        seed = load_bundled_seeds()[0]
        repository.save_release_state(
            _slot(seed.game.entity_id, slot_id="slot:one"), expected_revision=0
        )
        repository.save_release_state(
            _slot(seed.game.entity_id, slot_id="slot:two", revision=2),
            expected_revision=1,
        )
        now = _at(28)
        repository.enqueue_knowledge_job(
            idempotency_key="official_daily_probe:{}:global:all:2026-08-28".format(
                seed.game.entity_id
            ),
            job_kind="official_daily_probe",
            entity_id=seed.game.entity_id,
            request={
                "game_entity_id": seed.game.entity_id,
                "region": "global",
                "platform": "all",
                "date_or_boundary": "2026-08-28",
                "sources": seed.manifest["official_sources"],
            },
            next_attempt_at=now,
            now=now,
        )
        claimed = repository.claim_due_knowledge_job(now)
        assert claimed is not None
        return repository, claimed, now

    repository, claimed, now = prepared_repository(tmp_path / "success")
    asyncio.run(
        KnowledgeJobService(repository, probe=_Probe(), clock=lambda: now)._run_job(
            claimed
        )
    )
    assert repository.knowledge_jobs()[0].status == "completed"
    assert {
        slot.official_checked_at
        for slot in repository.load_release_state(
            load_bundled_seeds()[0].game.entity_id, "global", "all"
        )
    } == {now}

    failed_repository, failed_claimed, failed_now = prepared_repository(
        tmp_path / "rollback"
    )
    seed_source_id = load_bundled_seeds()[0].official_sources[0].source_id
    with connect_database(failed_repository.path) as db:
        old_hash = db.execute(
            "SELECT content_hash FROM knowledge_sources WHERE source_id=?",
            (seed_source_id,),
        ).fetchone()[0]

    def fail_atomic_commit(*args, **kwargs):
        raise RuntimeError("injected atomic failure")

    monkeypatch.setattr(
        failed_repository,
        "_write_release_state_in_transaction",
        fail_atomic_commit,
    )
    asyncio.run(
        KnowledgeJobService(
            failed_repository, probe=_Probe(), clock=lambda: failed_now
        )._run_job(failed_claimed)
    )
    assert failed_repository.knowledge_jobs()[0].status == "retry"
    assert {
        slot.official_checked_at
        for slot in failed_repository.load_release_state(
            load_bundled_seeds()[0].game.entity_id, "global", "all"
        )
    } == {1}
    with connect_database(failed_repository.path) as db:
        assert db.execute(
            "SELECT content_hash FROM knowledge_sources WHERE source_id=?",
            (seed_source_id,),
        ).fetchone()[0] == old_hash
