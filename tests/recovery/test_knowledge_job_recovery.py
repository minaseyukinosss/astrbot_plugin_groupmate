from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from groupmate.social_runtime.knowledge.repository import KnowledgeRepository
from groupmate.social_runtime.knowledge.seeds import SeedImporter, load_bundled_seeds
from groupmate.social_runtime.knowledge.sources import OfficialProbeResult
from groupmate.social_runtime.persistence.schema import connect_database
from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.settings import SocialRuntimeSettings


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
    claimed_id = claimed.job_id

    recovered = KnowledgeJobService(repository, probe=_UnavailableProbe(), clock=lambda: now)

    async def restart():
        await recovered.start()
        await _settle()
        await recovered.close()

    asyncio.run(restart())
    retry = next(
        job for job in repository.knowledge_jobs() if job.job_id == claimed_id
    )
    assert retry.status == "retry"
    assert retry.attempt == 2
    assert retry.diagnostic_code == "official_probe_unavailable"

    with connect_database(repository.path) as db:
        db.execute(
            "UPDATE knowledge_jobs SET next_attempt_at=? WHERE job_id<>? "
            "AND status IN ('pending','retry')",
            (retry.next_attempt_at + 86_400, claimed_id),
        )

    blocking = _BlockingProbe()
    live = KnowledgeJobService(repository, probe=blocking, clock=lambda: retry.next_attempt_at)

    async def cancel():
        await live.start()
        await live.wake()
        await blocking.started.wait()
        await live.close()

    asyncio.run(cancel())
    cancelled = next(
        job for job in repository.knowledge_jobs() if job.job_id == claimed_id
    )
    assert cancelled.status == "retry"
    assert cancelled.diagnostic_code == "official_probe_cancelled"

    with connect_database(repository.path) as db:
        db.execute(
            "UPDATE knowledge_jobs SET status='running',attempt=99 WHERE job_id=?",
            (claimed_id,),
        )
    capped = next(
        job for job in repository.knowledge_jobs() if job.job_id == claimed_id
    )
    KnowledgeJobService(
        repository, probe=_UnavailableProbe(), clock=lambda: retry.next_attempt_at
    )._retry(capped, "official_probe_failed")
    assert next(
        job for job in repository.knowledge_jobs() if job.job_id == claimed_id
    ).next_attempt_at == retry.next_attempt_at + 3600


def test_bridge_cleanup_orders_jobs_probe_and_all_remaining_resources(tmp_path, monkeypatch):
    calls: list[str] = []

    class Closer:
        def __init__(self, name: str, *, fails: bool = False) -> None:
            self.name = name
            self.fails = fails

        async def close(self):
            calls.append(self.name)
            if self.fails:
                raise RuntimeError(self.name)

    settings = SocialRuntimeSettings.from_mapping({"runtime_mode": "SHADOW"})
    bridge = AstrBotSocialRuntimeBridge(object(), settings, tmp_path)
    bridge._knowledge_job_service = Closer("jobs", fails=True)
    bridge.official_source_probe = Closer("probe")
    bridge._knowledge_service = Closer("knowledge")
    bridge._profile_service = Closer("profile", fails=True)
    bridge._manager = Closer("manager")
    bridge._cognition_client = Closer("cognition")
    bridge._profile_client = Closer("profile-client")

    with pytest.raises(RuntimeError, match="jobs"):
        asyncio.run(bridge.close())
    assert calls == [
        "jobs",
        "probe",
        "knowledge",
        "profile",
        "manager",
        "cognition",
        "profile-client",
    ]

    import groupmate.adapters.astrbot_bridge as bridge_module

    class FailingManager(Closer):
        def __init__(self, **kwargs) -> None:
            super().__init__("manager")

        async def start(self):
            raise RuntimeError("start failed")

    class Job(Closer):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__("jobs")

    class Probe(Closer):
        @property
        def available(self) -> bool:
            return False

    monkeypatch.setattr(bridge_module, "SocialRuntimeManager", FailingManager)
    monkeypatch.setattr(bridge_module, "KnowledgeJobService", Job)
    partial_dir = tmp_path / "partial"
    partial_dir.mkdir()
    partial = AstrBotSocialRuntimeBridge(
        object(),
        SocialRuntimeSettings.from_mapping(
            {
                "runtime_mode": "SHADOW",
                "enabled_groups": ["group-1"],
                "generation_provider": "provider:text",
                "profile_enabled": False,
            }
        ),
        partial_dir,
        cognition_client_factory=lambda settings: Closer("cognition"),
    )
    partial.official_source_probe = Probe("probe")
    partial_start = len(calls)
    with pytest.raises(RuntimeError, match="start failed"):
        asyncio.run(partial.start())
    assert calls[partial_start:] == ["jobs", "probe", "manager", "cognition"]
