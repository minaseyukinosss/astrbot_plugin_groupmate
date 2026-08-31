"""Persistent, SHADOW-only refresh work for bounded official-source probes."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from .learning import GameKnowledgeLearningWorker
from .repository import KnowledgeJobRecord, KnowledgeRepository
from .search import DiscoverySearchPort
from .seeds import load_bundled_seeds
from .sources import (
    OfficialProbeRequest,
    OfficialSourceDefinition,
    OfficialSourceProbePort,
)


_REGION = "global"
_PLATFORM = "all"
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_DAY_SECONDS = 24 * 60 * 60
_JITTER_SECONDS = 20 * 60
_BACKOFF_BASE_SECONDS = 60
_BACKOFF_CAP_SECONDS = 60 * 60


def _jitter(idempotency_key: str) -> int:
    return int(hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16], 16) % (
        _JITTER_SECONDS + 1
    )


class KnowledgeJobService:
    """One durable worker; every probe happens outside SQLite transactions."""

    def __init__(
        self,
        repository: KnowledgeRepository,
        *,
        probe: OfficialSourceProbePort,
        discovery_search: DiscoverySearchPort | None = None,
        clock: Callable[[], float],
    ) -> None:
        self._repository = repository
        self._probe = probe
        self._learning = (
            None
            if discovery_search is None
            else GameKnowledgeLearningWorker(
                repository,
                discovery_search=discovery_search,
                clock=clock,
            )
        )
        self._clock = clock
        self._accepting = False
        self._wake_event = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._accepting:
            return
        self._accepting = True
        self._repository.recover_running_knowledge_jobs(self._now())
        await self.wake()
        self._worker = asyncio.create_task(
            self._run(), name="groupmate-knowledge-jobs"
        )

    async def wake(self) -> None:
        if not self._accepting and self._worker is not None:
            return
        self._schedule_due_work(self._now())
        self._wake_event.set()

    async def close(self) -> None:
        self._accepting = False
        self._wake_event.set()
        worker = self._worker
        self._worker = None
        if worker is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(worker), timeout=2.0)
        except asyncio.TimeoutError:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    def health(self) -> dict[str, object]:
        jobs = self._repository.knowledge_jobs()
        return {
            "accepting": self._accepting,
            "worker_running": self._worker is not None and not self._worker.done(),
            "pending_or_retry": sum(
                item.status in {"pending", "retry"} for item in jobs
            ),
            "running": sum(item.status == "running" for item in jobs),
        }

    async def _run(self) -> None:
        while self._accepting:
            now = self._now()
            self._schedule_due_work(now)
            job = self._repository.claim_due_knowledge_job(now)
            if job is not None:
                await self._run_job(job)
                continue
            self._wake_event.clear()
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=60.0)
            except asyncio.TimeoutError:
                pass

    async def _run_job(self, job: KnowledgeJobRecord) -> None:
        if job.job_kind == "unknown_entity_learning":
            await self._run_learning_job(job)
            return
        try:
            request = self._request_from_job(job)
            result = await self._probe.probe(request)
        except asyncio.CancelledError:
            self._retry(job, "official_probe_cancelled")
            raise
        except Exception:
            self._retry(job, "official_probe_failed")
            return
        if result.status != "complete":
            self._retry(job, result.diagnostic_code or "official_probe_failed")
            return
        try:
            self._repository.complete_official_probe_job(
                job.job_id,
                evidence=result.evidence,
                observed_at=request.requested_at,
            )
        except asyncio.CancelledError:
            self._retry(job, "official_probe_cancelled")
            raise
        except Exception:
            self._retry(job, "official_probe_failed")

    async def _run_learning_job(self, job: KnowledgeJobRecord) -> None:
        if self._learning is None:
            self._retry(job, "learning_search_adapter_unavailable")
            return
        try:
            outcome = await self._learning.learn(job)
        except asyncio.CancelledError:
            self._retry(job, "knowledge_learning_cancelled")
            raise
        except Exception:
            self._retry(job, "knowledge_learning_failed")
            return
        if outcome.status != "complete":
            self._retry(
                job,
                outcome.diagnostic_code or "knowledge_learning_incomplete",
            )
            return
        self._repository.complete_knowledge_job(job.job_id, self._now())

    def _retry(self, job: KnowledgeJobRecord, diagnostic_code: str) -> None:
        delay = min(
            _BACKOFF_CAP_SECONDS,
            _BACKOFF_BASE_SECONDS * (2 ** max(0, job.attempt - 1)),
        )
        now = self._now()
        self._repository.retry_knowledge_job(
            job.job_id,
            next_attempt_at=now + delay,
            diagnostic_code=diagnostic_code,
            now=now,
        )

    def _schedule_due_work(self, now: int) -> None:
        date = datetime.fromtimestamp(now, tz=_SHANGHAI).date().isoformat()
        midnight = int(
            datetime.fromisoformat(date).replace(tzinfo=_SHANGHAI).timestamp()
        )
        for seed in load_bundled_seeds():
            game_id = seed.game.entity_id
            sources = self._repository.current_official_source_registry(
                seed.manifest["official_sources"]
            )
            if (
                self._daily_refresh_due(game_id, now)
                and not self._repository.has_outstanding_daily_refresh(
                    entity_id=game_id, region=_REGION, platform=_PLATFORM
                )
            ):
                key = self._job_key("official_daily_probe", game_id, date)
                self._enqueue(
                    key=key,
                    kind="official_daily_probe",
                    game_id=game_id,
                    date_or_boundary=date,
                    sources=sources,
                    due_at=max(0, midnight + _jitter(key)),
                    now=now,
                )
            for slot in self._repository.load_release_state(
                game_id, _REGION, _PLATFORM
            ):
                if slot.release_at is None or now < slot.release_at:
                    continue
                boundary = str(slot.release_at)
                key = self._job_key(
                    "time_boundary_revalidation", game_id, boundary
                )
                self._enqueue(
                    key=key,
                    kind="time_boundary_revalidation",
                    game_id=game_id,
                    date_or_boundary=boundary,
                    sources=sources,
                    due_at=slot.release_at,
                    now=now,
                )

    def _daily_refresh_due(self, game_id: str, now: int) -> bool:
        successful_at = self._repository.latest_completed_knowledge_job_at(
            job_kind="official_daily_probe",
            entity_id=game_id,
            region=_REGION,
            platform=_PLATFORM,
        )
        return successful_at is None or now - successful_at >= _DAY_SECONDS

    def _enqueue(
        self,
        *,
        key: str,
        kind: str,
        game_id: str,
        date_or_boundary: str,
        sources: object,
        due_at: int,
        now: int,
    ) -> None:
        self._repository.enqueue_knowledge_job(
            idempotency_key=key,
            job_kind=kind,
            entity_id=game_id,
            request={
                "game_entity_id": game_id,
                "region": _REGION,
                "platform": _PLATFORM,
                "date_or_boundary": date_or_boundary,
                "sources": sources,
            },
            next_attempt_at=due_at,
            now=now,
        )

    @staticmethod
    def _job_key(kind: str, game_id: str, date_or_boundary: str) -> str:
        return "{}:{}:{}:{}:{}".format(
            kind, game_id, _REGION, _PLATFORM, date_or_boundary
        )

    def _request_from_job(self, job: KnowledgeJobRecord) -> OfficialProbeRequest:
        request = job.request
        sources = tuple(
            OfficialSourceDefinition.create(
                source_id=item["source_id"],
                game_entity_id=request["game_entity_id"],
                publisher=item["publisher"],
                canonical_url=item["url"],
                required=item["required"],
            )
            for item in request["sources"]
        )
        return OfficialProbeRequest.create(
            request_id=job.job_id,
            game_entity_id=request["game_entity_id"],
            query_intent="version_state",
            sources=sources,
            region=request["region"],
            platform=request["platform"],
            requested_at=self._now(),
        )

    def _now(self) -> int:
        return int(self._clock())


__all__ = ("KnowledgeJobService",)
