"""Non-blocking profile observation and background processing service."""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from typing import Callable

from ...adapters.deepseek_profile import ProfileModelError
from ..contracts import SocialEventEnvelope
from .contracts import ProfileObservation
from .extractor import ProfileExtractor
from .identity import IdentityService
from .repository import ProfileRepository
from .snapshot import SnapshotBuilder


class ProfileService:
    def __init__(
        self,
        *,
        repository: ProfileRepository,
        extractor: ProfileExtractor | None,
        persona_id: str,
        group_ids: tuple[str, ...],
        batch_size: int = 20,
        interval_seconds: int = 600,
        style_service: object | None = None,
        clock: Callable[[], float] | None = None,
        snapshot_builder: SnapshotBuilder | None = None,
    ) -> None:
        self.repository = repository
        self.extractor = extractor
        self.identity_service = IdentityService(repository)
        self.persona_id = str(persona_id)
        self.group_ids = tuple(dict.fromkeys(str(item) for item in group_ids))
        self.batch_size = max(1, min(20, int(batch_size)))
        self.interval_seconds = max(10, int(interval_seconds))
        self.style_service = style_service
        self.clock = time.time if clock is None else clock
        self.snapshot_builder = snapshot_builder or SnapshotBuilder()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def observe(self, event: SocialEventEnvelope) -> bool:
        if (
            event.event_type != "platform.message"
            or not event.group_id
            or not event.actor_id
            or event.persona_id != self.persona_id
            or event.group_id not in self.group_ids
        ):
            return False
        self.identity_service.observe(event)
        inserted = self.repository.enqueue_observation(
            ProfileObservation(
                event_id=event.event_id,
                persona_id=event.persona_id,
                group_id=event.group_id,
                actor_id=event.actor_id,
                payload=dict(event.payload),
                occurred_at=event.occurred_at,
            )
        )
        if inserted and self.pending_count(event.group_id) >= self.batch_size:
            self._wake.set()
        if inserted and self.style_service is not None:
            wake = getattr(self.style_service, "wake", None)
            if callable(wake):
                wake()
        return inserted

    async def process_due(self, *, now: int | None = None) -> None:
        if self.extractor is None:
            return
        decision_now = int(self.clock()) if now is None else int(now)
        for group_id in self.group_ids:
            observations = self.repository.claim_observations(
                self.persona_id,
                group_id,
                limit=self.batch_size,
                now=decision_now,
            )
            if not observations:
                continue
            event_ids = tuple(item.event_id for item in observations)
            try:
                result = await self.extractor.extract(observations)
            except ProfileModelError as exc:
                attempt = max(item.attempt for item in observations)
                delay = (60, 300, 1800)[min(max(attempt - 1, 0), 2)]
                self.repository.complete_observations(
                    event_ids,
                    status="retry",
                    diagnostic_code=exc.code,
                    next_attempt_at=decision_now + delay,
                )
                continue
            for fact in result.facts:
                self.repository.put_fact(fact)
            for episode in result.episodes:
                self.repository.put_episode(episode)
            for edge in result.edges:
                self.repository.put_edge(edge)
            self._refresh_snapshots(
                observations,
                result=result,
                generated_at=decision_now,
            )
            self.repository.complete_observations(
                event_ids,
                status=(
                    "discarded"
                    if result.diagnostic_code and not result.facts
                    else "completed"
                ),
                diagnostic_code=result.diagnostic_code,
            )

    def _refresh_snapshots(
        self,
        observations: tuple[ProfileObservation, ...],
        *,
        result,
        generated_at: int,
    ) -> None:
        subject_ids = {
            item.subject_id for item in result.facts
        } | {
            participant
            for item in result.episodes
            for participant in item.participants
        } | {
            actor_id
            for item in result.edges
            for actor_id in (item.source_member_id, item.target_member_id)
        }
        observations_by_actor = {
            item.actor_id: item for item in observations
        }
        source_revision = max(
            (item.occurred_at for item in observations),
            default=generated_at,
        )
        for subject_id in subject_ids:
            observation = observations_by_actor.get(subject_id)
            if observation is None:
                continue
            platform = str(observation.payload.get("platform") or "qq")
            identity = self.identity_service.resolve(
                self.persona_id, platform, subject_id
            )
            if identity is None:
                continue
            group_id = observation.group_id
            snapshot = self.snapshot_builder.build(
                identity,
                group_id=group_id,
                facts=self.repository.facts(
                    self.persona_id, group_id, subject_id
                ),
                episodes=self.repository.episodes(
                    self.persona_id, group_id, subject_id
                ),
                edges=self.repository.edges(
                    self.persona_id, group_id, subject_id
                ),
                source_revision=source_revision,
                generated_at=generated_at,
            )
            self.repository.put_snapshot(snapshot)

    def pending_count(self, group_id: str) -> int:
        return self.repository.pending_observation_count(
            self.persona_id, str(group_id)
        )

    def diagnostics(self, group_id: str) -> tuple[str, ...]:
        return self.repository.observation_diagnostics(
            self.persona_id, str(group_id)
        )

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())
        if self.style_service is not None:
            start = getattr(self.style_service, "start", None)
            if callable(start):
                await start()

    async def close(self) -> None:
        if self.style_service is not None:
            close = getattr(self.style_service, "close", None)
            if callable(close):
                await close()
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=self.interval_seconds
                )
            except TimeoutError:
                pass
            self._wake.clear()
            await self.process_due()


__all__ = ("ProfileService",)
