"""Bounded, recoverable coordination for reply-time knowledge enrichment."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable

from .admission import KnowledgeEvidenceAdmission
from .contracts import (
    KnowledgeFact,
    KnowledgeNeed,
    KnowledgeSnapshot,
    SourceEvidence,
    TopicUnderstandingFrame,
)
from .repository import KnowledgeJobRecord, KnowledgeRepository
from .search import DiscoverySearchPort, DiscoverySearchResult, SearchRequest
from .snapshot import KnowledgeSnapshotBuilder
from .sources import (
    OfficialProbeRequest,
    OfficialProbeResult,
    OfficialSourceProbePort,
)


class EnrichmentLane(str, Enum):
    DIRECT = "DIRECT"
    CONTINUATION = "CONTINUATION"
    AMBIENT = "AMBIENT"


def _text(value: object, name: str, maximum: int, *, optional: bool = False):
    normalized = " ".join(str(value or "").split())
    if not normalized:
        if optional:
            return None
        raise ValueError(f"{name} must not be empty")
    if len(normalized) > maximum:
        raise ValueError(f"{name} is too long")
    return normalized


def _time_value(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a non-negative timestamp")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a non-negative timestamp") from error
    if normalized < 0:
        raise ValueError(f"{name} must be a non-negative timestamp")
    return normalized


@dataclass(frozen=True)
class SceneGuard:
    group_id: str
    scene_version: int
    target_id: str
    lease_id: str | None
    lease_expires_at: float | None
    intention_id: str
    intention_expires_at: float

    @classmethod
    def create(cls, **values: object) -> "SceneGuard":
        scene_version = int(values.get("scene_version", -1))
        if scene_version < 0:
            raise ValueError("scene_version must not be negative")
        lease_id = _text(values.get("lease_id"), "lease_id", 128, optional=True)
        lease_expiry = values.get("lease_expires_at")
        if (lease_id is None) != (lease_expiry is None):
            raise ValueError("lease identity and expiry must be supplied together")
        return cls(
            group_id=_text(values.get("group_id"), "group_id", 128),
            scene_version=scene_version,
            target_id=_text(values.get("target_id"), "target_id", 128),
            lease_id=lease_id,
            lease_expires_at=(
                None
                if lease_expiry is None
                else _time_value(lease_expiry, "lease_expires_at")
            ),
            intention_id=_text(
                values.get("intention_id"), "intention_id", 128
            ),
            intention_expires_at=_time_value(
                values.get("intention_expires_at"), "intention_expires_at"
            ),
        )


@dataclass(frozen=True)
class SceneGuardCheck:
    is_valid: bool
    diagnostic_code: str | None

    @classmethod
    def valid(cls) -> "SceneGuardCheck":
        return cls(True, None)

    @classmethod
    def invalid(cls, diagnostic_code: str) -> "SceneGuardCheck":
        return cls(False, _text(diagnostic_code, "diagnostic_code", 64))


@dataclass(frozen=True)
class EnrichmentRequest:
    request_id: str
    lane: EnrichmentLane
    scene_guard: SceneGuard
    frame: TopicUnderstandingFrame
    need: KnowledgeNeed
    official_request: OfficialProbeRequest
    search_request: SearchRequest | None
    requested_at: float
    soft_deadline: float
    hard_deadline: float
    version_slot_id: str | None
    version_state_revision: int
    region: str | None
    platform: str | None

    @classmethod
    def create(cls, **values: object) -> "EnrichmentRequest":
        try:
            lane = EnrichmentLane(values.get("lane"))
        except (TypeError, ValueError) as error:
            raise ValueError("lane is unsupported") from error
        guard = values.get("scene_guard")
        frame = values.get("frame")
        need = values.get("need")
        official = values.get("official_request")
        search = values.get("search_request")
        if not isinstance(guard, SceneGuard):
            raise ValueError("scene_guard is invalid")
        if not isinstance(frame, TopicUnderstandingFrame):
            raise ValueError("frame is invalid")
        if not isinstance(need, KnowledgeNeed):
            raise ValueError("need is invalid")
        if not isinstance(official, OfficialProbeRequest):
            raise ValueError("official_request is invalid")
        if search is not None and not isinstance(search, SearchRequest):
            raise ValueError("search_request is invalid")
        requested_at = _time_value(values.get("requested_at"), "requested_at")
        soft = _time_value(values.get("soft_deadline"), "soft_deadline")
        hard = _time_value(values.get("hard_deadline"), "hard_deadline")
        if not requested_at < soft <= hard:
            raise ValueError("enrichment deadlines are invalid")
        if lane in {EnrichmentLane.DIRECT, EnrichmentLane.CONTINUATION} and (
            hard - requested_at > 5
        ):
            raise ValueError("interactive enrichment cannot exceed 5 seconds")
        if lane is EnrichmentLane.CONTINUATION and guard.lease_id is None:
            raise ValueError("continuation requires a lease guard")
        if official.game_entity_id not in frame.game_ids:
            raise ValueError("official request game is outside the topic frame")
        if search is not None and search.game_entity_id != official.game_entity_id:
            raise ValueError("provider requests must target the same game")
        revision = int(values.get("version_state_revision", 0))
        if revision < 0:
            raise ValueError("version_state_revision must not be negative")
        return cls(
            request_id=_text(values.get("request_id"), "request_id", 128),
            lane=lane,
            scene_guard=guard,
            frame=frame,
            need=need,
            official_request=official,
            search_request=search,
            requested_at=requested_at,
            soft_deadline=soft,
            hard_deadline=hard,
            version_slot_id=_text(
                values.get("version_slot_id"),
                "version_slot_id",
                128,
                optional=True,
            ),
            version_state_revision=revision,
            region=_text(values.get("region"), "region", 48, optional=True),
            platform=_text(
                values.get("platform"), "platform", 48, optional=True
            ),
        )


@dataclass(frozen=True)
class EnrichmentResult:
    status: str
    diagnostic_code: str | None
    knowledge_committed: bool
    reply_still_valid: bool
    cache_hit: bool
    snapshot: KnowledgeSnapshot | None
    source_domains: tuple[str, ...]
    intent_hash: str


@dataclass(frozen=True)
class _SharedOutcome:
    status: str
    diagnostic_code: str | None
    knowledge_committed: bool
    cache_hit: bool
    source_domains: tuple[str, ...]


@dataclass(frozen=True)
class _ProviderOutcome:
    status: str
    evidence: tuple[SourceEvidence, ...]
    source_domains: tuple[str, ...]
    diagnostic_code: str | None


def normalized_enrichment_key(request: EnrichmentRequest) -> str:
    if not isinstance(request, EnrichmentRequest):
        raise TypeError("request must be EnrichmentRequest")
    search = request.search_request
    payload = {
        "game_entity_id": request.official_request.game_entity_id,
        "query_intents": sorted(request.need.query_intents),
        "region": request.region,
        "platform": request.platform,
        "official_sources": sorted(
            (item.source_id, item.canonical_url)
            for item in request.official_request.sources
        ),
        "search_scope": (
            None
            if search is None
            else {
                "game_name": search.game_name,
                "entity_id": search.entity_id,
                "entity_name": search.entity_name,
                "entity_hint": search.entity_hint,
                "max_results": search.max_results,
            }
        ),
    }
    packed = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(packed).hexdigest()


class KnowledgeEnrichmentCoordinator:
    def __init__(
        self,
        repository: KnowledgeRepository,
        *,
        official_probe: OfficialSourceProbePort,
        discovery_search: DiscoverySearchPort,
        scene_guard_validator: Callable[[SceneGuard, int], SceneGuardCheck],
        clock: Callable[[], float],
        max_provider_concurrency: int = 2,
        hourly_limit: int = 20,
        daily_limit: int = 100,
        cache_ttl_seconds: int = 600,
        admission: KnowledgeEvidenceAdmission | None = None,
        snapshot_builder: KnowledgeSnapshotBuilder | None = None,
        fact_loader: Callable[
            [TopicUnderstandingFrame, KnowledgeNeed, int], Iterable[KnowledgeFact]
        ]
        | None = None,
    ) -> None:
        if max_provider_concurrency < 1 or hourly_limit < 1 or daily_limit < 1:
            raise ValueError("enrichment limits must be positive")
        self._repository = repository
        self._official_probe = official_probe
        self._discovery_search = discovery_search
        self._guard_validator = scene_guard_validator
        self._clock = clock
        self._hourly_limit = int(hourly_limit)
        self._daily_limit = int(daily_limit)
        self._cache_ttl = int(cache_ttl_seconds)
        self._admission = admission or KnowledgeEvidenceAdmission()
        self._snapshot_builder = snapshot_builder or KnowledgeSnapshotBuilder()
        self._fact_loader = fact_loader or (lambda frame, need, now: ())
        self._provider_gate = asyncio.Semaphore(max_provider_concurrency)
        self._flights: dict[str, asyncio.Task[_SharedOutcome]] = {}
        recovery_now = self._now()
        self._repository.recover_enrichment_jobs(
            recovery_now, stale_before=max(0, recovery_now - 5)
        )

    async def enrich(self, request: EnrichmentRequest) -> EnrichmentResult:
        if not isinstance(request, EnrichmentRequest):
            raise TypeError("request must be EnrichmentRequest")
        intent_hash = normalized_enrichment_key(request)
        if request.lane is EnrichmentLane.AMBIENT:
            return self._result(
                intent_hash,
                status="disabled",
                diagnostic="ambient_search_disabled",
            )
        initial_now = self._now()
        initial_expiry = self._guard_expiry(request.scene_guard, initial_now)
        if initial_expiry is not None or request.need.expires_at <= initial_now:
            return self._result(
                intent_hash,
                status="expired",
                diagnostic=initial_expiry or "knowledge_need_expired",
            )
        if self._now_float() >= request.hard_deadline:
            return self._result(
                intent_hash,
                status="timed_out",
                diagnostic="enrichment_deadline_exceeded",
            )

        task = self._flights.get(intent_hash)
        if task is None:
            task = asyncio.create_task(
                self._run_shared_and_cleanup(intent_hash, request),
                name=f"knowledge-enrichment:{intent_hash[:12]}",
            )
            self._flights[intent_hash] = task
        remaining = request.hard_deadline - self._now_float()
        if remaining <= 0:
            return self._result(
                intent_hash,
                status="timed_out",
                diagnostic="enrichment_deadline_exceeded",
            )
        try:
            shared = await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
        except asyncio.TimeoutError:
            return self._result(
                intent_hash,
                status="timed_out",
                diagnostic="enrichment_deadline_exceeded",
            )
        except Exception:
            return self._result(
                intent_hash,
                status="failed",
                diagnostic="enrichment_failed",
            )

        now = self._now()
        if self._now_float() >= request.hard_deadline:
            return self._result(
                intent_hash,
                status=shared.status,
                diagnostic="enrichment_deadline_exceeded",
                committed=shared.knowledge_committed,
                cache_hit=shared.cache_hit,
                domains=shared.source_domains,
            )
        expiry_diagnostic = self._guard_expiry(request.scene_guard, now)
        if expiry_diagnostic is not None or request.need.expires_at <= now:
            return self._result(
                intent_hash,
                status=shared.status,
                diagnostic=expiry_diagnostic or "knowledge_need_expired",
                committed=shared.knowledge_committed,
                cache_hit=shared.cache_hit,
                domains=shared.source_domains,
            )
        guard = self._guard_validator(request.scene_guard, now)
        if not isinstance(guard, SceneGuardCheck):
            raise TypeError("scene guard validator must return SceneGuardCheck")
        if not guard.is_valid:
            return self._result(
                intent_hash,
                status=shared.status,
                diagnostic=guard.diagnostic_code,
                committed=shared.knowledge_committed,
                cache_hit=shared.cache_hit,
                domains=shared.source_domains,
            )
        snapshot = self._snapshot_builder.build(
            request.frame,
            request.need,
            self._fact_loader(request.frame, request.need, now),
            now,
            version_slot_id=request.version_slot_id,
            region=request.region,
            platform=request.platform,
            version_state_revision=request.version_state_revision,
        )
        return EnrichmentResult(
            status=shared.status,
            diagnostic_code=shared.diagnostic_code,
            knowledge_committed=shared.knowledge_committed,
            reply_still_valid=shared.knowledge_committed,
            cache_hit=shared.cache_hit,
            snapshot=snapshot if shared.knowledge_committed else None,
            source_domains=shared.source_domains,
            intent_hash=intent_hash,
        )

    async def _run_shared_and_cleanup(
        self, intent_hash: str, request: EnrichmentRequest
    ) -> _SharedOutcome:
        try:
            return await self._run_shared(intent_hash, request)
        finally:
            current = asyncio.current_task()
            if self._flights.get(intent_hash) is current:
                self._flights.pop(intent_hash, None)

    async def _run_shared(
        self, intent_hash: str, request: EnrichmentRequest
    ) -> _SharedOutcome:
        started = time.monotonic()
        now = self._now()
        cached = self._repository.successful_enrichment_cache(
            intent_hash, now=now, ttl_seconds=self._cache_ttl
        )
        if cached is not None:
            self._repository.record_enrichment_usage(
                intent_hash=intent_hash,
                source_domains=cached.source_domains,
                latency_ms=int((time.monotonic() - started) * 1000),
                cache_hit=True,
                result_kind="enrichment_cache_hit",
                diagnostic_code=None,
                now=now,
            )
            return _SharedOutcome(
                "complete", None, True, True, cached.source_domains
            )

        job = self._repository.acquire_enrichment_job(
            request_hash=intent_hash,
            entity_id=request.official_request.game_entity_id,
            query_intents=request.need.query_intents,
            now=now,
        )
        if job.status == "running" and job.request.get("request_hash") != intent_hash:
            raise RuntimeError("enrichment job identity mismatch")
        evidence = list(self._staged_evidence(job))
        official_complete = bool(job.request.get("official_complete", False))
        discovery_required = "rumor_next_version" in request.need.query_intents
        discovery_complete = bool(job.request.get("discovery_complete", False))
        diagnostic = job.diagnostic_code
        domains = list(dict.fromkeys(item.domain for item in evidence))

        if not official_complete:
            official = await self._call_official(
                request.official_request, intent_hash, request.hard_deadline
            )
            evidence.extend(official.evidence)
            domains.extend(
                item for item in official.source_domains if item not in domains
            )
            official_complete = official.status == "complete"
            diagnostic = official.diagnostic_code
            job = self._repository.stage_enrichment_job(
                job.job_id,
                evidence=evidence,
                official_complete=official_complete,
                discovery_complete=(not discovery_required),
                diagnostic_code=diagnostic,
                now=self._now(),
            )

        if discovery_required and not discovery_complete:
            if request.search_request is None:
                diagnostic = "search_unavailable"
            else:
                discovery = await self._call_discovery(
                    request.search_request, intent_hash, request.hard_deadline
                )
                evidence.extend(discovery.evidence)
                domains.extend(
                    item for item in discovery.source_domains if item not in domains
                )
                discovery_complete = discovery.status == "complete"
                if discovery.diagnostic_code is not None:
                    diagnostic = discovery.diagnostic_code
            job = self._repository.stage_enrichment_job(
                job.job_id,
                evidence=evidence,
                official_complete=official_complete,
                discovery_complete=discovery_complete,
                diagnostic_code=diagnostic,
                now=self._now(),
            )

        admitted = self._admission.commit(
            self._repository,
            game_entity_id=request.official_request.game_entity_id,
            evidence=evidence,
        )
        self._repository.complete_enrichment_job(job.job_id, now=self._now())
        success = official_complete and (
            discovery_complete if discovery_required else True
        )
        status = "complete" if success else "partial"
        if success:
            diagnostic = None
            self._repository.record_enrichment_usage(
                intent_hash=intent_hash,
                source_domains=admitted.source_domains,
                latency_ms=int((time.monotonic() - started) * 1000),
                cache_hit=False,
                result_kind="enrichment_success",
                diagnostic_code=None,
                now=self._now(),
            )
        else:
            self._repository.record_enrichment_usage(
                intent_hash=intent_hash,
                source_domains=admitted.source_domains,
                latency_ms=int((time.monotonic() - started) * 1000),
                cache_hit=False,
                result_kind="enrichment_partial",
                diagnostic_code=diagnostic or "partial_result",
                now=self._now(),
            )
        return _SharedOutcome(
            status,
            diagnostic,
            bool(admitted.source_ids),
            False,
            admitted.source_domains,
        )

    async def _call_official(
        self, request: OfficialProbeRequest, intent_hash: str, deadline: float
    ) -> _ProviderOutcome:
        return await self._provider_call(
            lambda: self._official_probe.probe(request),
            intent_hash=intent_hash,
            deadline=deadline,
            unpack=self._official_outcome,
            failure_diagnostic="official_probe_failed",
        )

    async def _call_discovery(
        self, request: SearchRequest, intent_hash: str, deadline: float
    ) -> _ProviderOutcome:
        return await self._provider_call(
            lambda: self._discovery_search.search(request),
            intent_hash=intent_hash,
            deadline=deadline,
            unpack=self._discovery_outcome,
            failure_diagnostic="search_failed",
        )

    async def _provider_call(
        self,
        invoke,
        *,
        intent_hash: str,
        deadline: float,
        unpack,
        failure_diagnostic: str,
    ) -> _ProviderOutcome:
        reservation = self._repository.reserve_provider_quota(
            intent_hash=intent_hash,
            now=self._now(),
            hourly_limit=self._hourly_limit,
            daily_limit=self._daily_limit,
        )
        if reservation is None:
            return _ProviderOutcome(
                "unavailable", (), (), "knowledge_budget_exhausted"
            )
        remaining = deadline - self._now_float()
        if remaining <= 0:
            self._repository.release_provider_quota(reservation)
            return _ProviderOutcome(
                "timed_out", (), (), "enrichment_deadline_exceeded"
            )
        acquired = False
        provider_started = False
        started = time.monotonic()
        try:
            await asyncio.wait_for(self._provider_gate.acquire(), timeout=remaining)
            acquired = True
            remaining = deadline - self._now_float()
            if remaining <= 0:
                self._provider_gate.release()
                acquired = False
                self._repository.release_provider_quota(reservation)
                return _ProviderOutcome(
                    "timed_out", (), (), "enrichment_deadline_exceeded"
                )
            try:
                provider_started = True
                raw = await asyncio.wait_for(invoke(), timeout=remaining)
                outcome = unpack(raw)
            except asyncio.TimeoutError:
                outcome = _ProviderOutcome(
                    "timed_out", (), (), "enrichment_deadline_exceeded"
                )
            except Exception:
                outcome = _ProviderOutcome("failed", (), (), failure_diagnostic)
            self._provider_gate.release()
            acquired = False
            self._repository.finish_provider_quota(
                reservation,
                source_domains=outcome.source_domains,
                latency_ms=int((time.monotonic() - started) * 1000),
                result_kind=f"provider_{outcome.status}",
                diagnostic_code=outcome.diagnostic_code,
            )
            return outcome
        except asyncio.CancelledError:
            if acquired:
                self._provider_gate.release()
                acquired = False
            if provider_started:
                self._repository.finish_provider_quota(
                    reservation,
                    source_domains=(),
                    latency_ms=int((time.monotonic() - started) * 1000),
                    result_kind="provider_cancelled",
                    diagnostic_code="enrichment_cancelled",
                )
            else:
                self._repository.release_provider_quota(reservation)
            raise
        except asyncio.TimeoutError:
            self._repository.release_provider_quota(reservation)
            return _ProviderOutcome(
                "timed_out", (), (), "enrichment_deadline_exceeded"
            )
        finally:
            if acquired:
                self._provider_gate.release()

    @staticmethod
    def _official_outcome(result: OfficialProbeResult) -> _ProviderOutcome:
        if not isinstance(result, OfficialProbeResult):
            raise TypeError("official provider returned an invalid result")
        return _ProviderOutcome(
            result.status,
            result.evidence,
            tuple(dict.fromkeys(item.domain for item in result.evidence)),
            result.diagnostic_code,
        )

    @staticmethod
    def _discovery_outcome(result: DiscoverySearchResult) -> _ProviderOutcome:
        if not isinstance(result, DiscoverySearchResult):
            raise TypeError("discovery provider returned an invalid result")
        return _ProviderOutcome(
            result.status,
            result.candidates,
            tuple(dict.fromkeys(item.domain for item in result.candidates)),
            result.diagnostic_code,
        )

    @staticmethod
    def _staged_evidence(job: KnowledgeJobRecord) -> tuple[SourceEvidence, ...]:
        values = job.request.get("staged_evidence", ())
        if not isinstance(values, list):
            raise ValueError("staged evidence payload is invalid")
        return tuple(SourceEvidence.create(**dict(item)) for item in values)

    @staticmethod
    def _result(
        intent_hash: str,
        *,
        status: str,
        diagnostic: str | None,
        committed: bool = False,
        cache_hit: bool = False,
        domains: tuple[str, ...] = (),
    ) -> EnrichmentResult:
        return EnrichmentResult(
            status=status,
            diagnostic_code=diagnostic,
            knowledge_committed=committed,
            reply_still_valid=False,
            cache_hit=cache_hit,
            snapshot=None,
            source_domains=domains,
            intent_hash=intent_hash,
        )

    def _now(self) -> int:
        return int(self._clock())

    def _now_float(self) -> float:
        return float(self._clock())

    @staticmethod
    def _guard_expiry(guard: SceneGuard, now: int) -> str | None:
        if guard.intention_expires_at <= now:
            return "intention_expired"
        if guard.lease_expires_at is not None and guard.lease_expires_at <= now:
            return "continuation_lease_expired"
        return None


__all__ = (
    "EnrichmentLane",
    "EnrichmentRequest",
    "EnrichmentResult",
    "KnowledgeEnrichmentCoordinator",
    "SceneGuard",
    "SceneGuardCheck",
    "normalized_enrichment_key",
)
