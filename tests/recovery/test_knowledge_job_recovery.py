from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from groupmate.social_runtime.knowledge.repository import KnowledgeRepository
from groupmate.social_runtime.knowledge.seeds import SeedImporter, load_bundled_seeds
from groupmate.social_runtime.knowledge.sources import OfficialProbeResult


def _now() -> int:
    return int(datetime(2026, 8, 28, 0, 30, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())


class _UnavailableProbe:
    async def probe(self, request):
        return OfficialProbeResult.create(
            request=request,
            status="unavailable",
            evidence=(),
            covered_source_ids=(),
            diagnostic_code="official_probe_unavailable",
        )


class _BlockingProbe:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def probe(self, request):
        self.started.set()
        await asyncio.Event().wait()


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


def test_restart_recovers_running_job_and_close_never_marks_it_successful(tmp_path):
    from groupmate.social_runtime.knowledge.jobs import KnowledgeJobService

    repository = KnowledgeRepository(tmp_path / "groupmate-social-runtime-v2.db")
    seed = load_bundled_seeds()[0]
    SeedImporter(repository, clock=_now).import_all((seed,))
    now = _now()
    initial = KnowledgeJobService(repository, probe=_UnavailableProbe(), clock=lambda: now)
    asyncio.run(initial.wake())
    claimed = repository.claim_due_knowledge_job(now)
    assert claimed is not None and claimed.status == "running" and claimed.attempt == 1

    recovered = KnowledgeJobService(repository, probe=_UnavailableProbe(), clock=lambda: now)

    async def restart():
        await recovered.start()
        await _settle()
        await recovered.close()

    asyncio.run(restart())
    retry = repository.knowledge_jobs()[0]
    assert retry.status == "retry"
    assert retry.attempt == 2
    assert retry.diagnostic_code == "official_probe_unavailable"

    blocking = _BlockingProbe()
    live = KnowledgeJobService(repository, probe=blocking, clock=lambda: retry.next_attempt_at)

    async def cancel():
        await live.start()
        await live.wake()
        await blocking.started.wait()
        await live.close()

    asyncio.run(cancel())
    cancelled = repository.knowledge_jobs()[0]
    assert cancelled.status == "retry"
    assert cancelled.diagnostic_code == "official_probe_cancelled"
