"""Non-blocking profile observation and background processing service."""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from dataclasses import replace
from typing import Callable

from ...adapters.deepseek_profile import ProfileModelError
from ..contracts import SocialEventEnvelope
from .contracts import ProfileKnownCognition, ProfileObservation
from .extractor import ProfileExtractor, referenced_actor_ids
from .group_portrait import GroupPortraitBuilder
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
        group_portrait_builder: GroupPortraitBuilder | None = None,
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
        self.group_portrait_builder = (
            group_portrait_builder or GroupPortraitBuilder()
        )
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._health = {
            group_id: {
                "last_attempt_at": None,
                "last_success_at": None,
                "last_diagnostic": None,
            }
            for group_id in self.group_ids
        }

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
        if inserted and (
            self.repository.pending_observation_count(
                event.persona_id, event.group_id, event.actor_id
            )
            >= max(1, min(6, self.batch_size))
            or self.pending_count(event.group_id) >= self.batch_size
        ):
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
            health = self._health[group_id]
            health["last_attempt_at"] = decision_now
            try:
                diagnostic = await self._process_batch(
                    observations, decision_now=decision_now
                )
            except ProfileModelError as exc:
                attempt = max(item.attempt for item in observations)
                delay = (60, 300, 1800)[min(max(attempt - 1, 0), 2)]
                self.repository.complete_observations(
                    event_ids,
                    status="retry",
                    diagnostic_code=exc.code,
                    next_attempt_at=decision_now + delay,
                )
                health["last_diagnostic"] = exc.code
                continue
            except Exception:
                # Do not expose exception text or let one malformed batch kill
                # the long-lived scheduler. The durable observation is retried.
                self.repository.complete_observations(
                    event_ids,
                    status="retry",
                    diagnostic_code="profile_worker_failed",
                    next_attempt_at=decision_now + 60,
                )
                health["last_diagnostic"] = "profile_worker_failed"
                continue
            health["last_success_at"] = decision_now
            health["last_diagnostic"] = diagnostic

    async def _process_batch(
        self,
        observations: tuple[ProfileObservation, ...],
        *,
        decision_now: int,
    ) -> str | None:
        """Extract and persist one claimed batch as an idempotent unit of work."""

        result = await self.extractor.extract(
            observations, known=self._known_cognition(observations)
        )
        event_ids = tuple(item.event_id for item in observations)
        merged_facts = []
        for fact in result.facts:
            existing = self.repository.fact(fact.fact_id)
            merged = (
                fact
                if existing is None
                else self.extractor.policy.reinforce_fact(existing, fact)
            )
            self.repository.upsert_fact(merged)
            merged_facts.append(merged)
        for fact_id in result.stale_fact_ids:
            current = self.repository.fact(fact_id)
            if current is None:
                continue
            self.repository.change_fact_status(
                fact_id,
                persona_id=current.persona_id,
                group_id=current.group_id,
                subject_id=current.subject_id,
                status="stale",
                audit_id=f"profile-stale:{fact_id}:{decision_now}",
                actor_id="profile_worker",
                created_at=decision_now,
            )
        for episode in result.episodes:
            existing = self.repository.episode(episode.episode_id)
            merged_episode = (
                episode
                if existing is None
                else self.extractor.policy.reinforce_episode(existing, episode)
            )
            self.repository.put_episode(merged_episode)
        merged_edges = []
        for edge in result.edges:
            existing = self.repository.edge(edge.edge_id)
            merged = (
                edge
                if existing is None
                else self.extractor.policy.reinforce_edge(existing, edge)
            )
            self.repository.put_edge(merged)
            merged_edges.append(merged)
        merged_result = replace(
            result,
            facts=tuple(merged_facts),
            edges=tuple(merged_edges),
        )
        self._refresh_snapshots(
            observations,
            result=merged_result,
            generated_at=decision_now,
        )
        group_id = observations[0].group_id
        self._refresh_group_portrait(
            group_id,
            source_revision=max(
                (item.occurred_at for item in observations),
                default=decision_now,
            ),
            generated_at=decision_now,
        )
        has_candidates = bool(
            result.facts
            or result.episodes
            or result.edges
            or result.stale_fact_ids
        )
        diagnostic = result.diagnostic_code or (
            None if has_candidates else "profile_no_candidates"
        )
        self.repository.complete_observations(
            event_ids,
            status=(
                "discarded"
                if result.diagnostic_code and not has_candidates
                else "completed"
            ),
            diagnostic_code=diagnostic,
        )
        return diagnostic

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
        platform = str(
            next(
                (
                    item.payload.get("platform")
                    for item in observations
                    if str(item.payload.get("platform") or "").strip()
                ),
                "qq",
            )
        )
        group_id = observations[0].group_id
        rival_summaries = tuple(
            dict.fromkeys(
                summary
                for snapshot in self.repository.snapshots_for_group(
                    self.persona_id, group_id
                )
                if snapshot.subject_id not in subject_ids
                for summary in (
                    *snapshot.individual_fingerprints,
                    *snapshot.preferences_and_boundaries,
                    *snapshot.group_roles,
                )
                if summary
            )
        )
        for subject_id in subject_ids:
            observation = observations_by_actor.get(subject_id)
            identity = self.identity_service.resolve(
                self.persona_id,
                str(
                    observation.payload.get("platform") or platform
                    if observation is not None
                    else platform
                ),
                subject_id,
            )
            if identity is None:
                continue
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
                rival_summaries=rival_summaries,
            )
            self.repository.put_snapshot(snapshot)

    def _known_cognition(
        self, observations: tuple[ProfileObservation, ...]
    ) -> ProfileKnownCognition:
        group_id = observations[0].group_id
        subjects = {item.actor_id for item in observations}
        for item in observations:
            subjects.update(referenced_actor_ids(item.payload))
        facts = []
        episodes = []
        for subject_id in tuple(subjects)[:8]:
            facts.extend(
                self.repository.facts(self.persona_id, group_id, subject_id)[:12]
            )
            episodes.extend(
                self.repository.episodes(
                    self.persona_id, group_id, subject_id
                )[:6]
            )
        edges = tuple(
            item
            for item in self.repository.edges(self.persona_id, group_id)
            if item.source_member_id in subjects
            or item.target_member_id in subjects
        )[:8]
        return ProfileKnownCognition(
            facts=tuple(facts[:32]),
            episodes=tuple(dict.fromkeys(episodes))[:12],
            edges=edges,
        )

    def _refresh_group_portrait(
        self,
        group_id: str,
        *,
        source_revision: int,
        generated_at: int,
    ) -> None:
        """Rebuild only privacy-safe group aggregates after each batch."""

        portrait = self.group_portrait_builder.build(
            persona_id=self.persona_id,
            group_id=group_id,
            member_snapshots=self.repository.snapshots_for_group(
                self.persona_id, group_id
            ),
            # Culture/topics need their own governed evidence source.  Private
            # member facts must never be repurposed as group-wide topics.
            culture=(),
            topic_counts={},
            activity_hours=self.repository.observation_hours(
                self.persona_id, group_id
            ),
            edges=self.repository.edges(self.persona_id, group_id),
            source_revision=source_revision,
            generated_at=generated_at,
        )
        self.repository.put_group_portrait(portrait)

    def pending_count(self, group_id: str) -> int:
        return self.repository.pending_observation_count(
            self.persona_id, str(group_id)
        )

    def diagnostics(self, group_id: str) -> tuple[str, ...]:
        return self.repository.observation_diagnostics(
            self.persona_id, str(group_id)
        )

    def health(self, group_id: str) -> dict[str, object]:
        """Expose bounded operational state without model output or errors."""

        normalized = str(group_id)
        health = self._health.get(normalized, {})
        task = self._task
        return {
            "enabled": self.extractor is not None,
            "task_running": bool(task is not None and not task.done()),
            "pending_count": self.pending_count(normalized),
            "last_attempt_at": health.get("last_attempt_at"),
            "last_success_at": health.get("last_success_at"),
            "last_diagnostic": health.get("last_diagnostic"),
        }

    def status(self, group_id: str) -> dict[str, object]:
        """Compatibility alias for callers that render a generic status."""

        return self.health(group_id)

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
            try:
                await self.process_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A failure outside a claimed batch is still visible and the
                # next interval remains scheduled.
                for health in self._health.values():
                    health["last_diagnostic"] = "profile_worker_failed"


__all__ = ("ProfileService",)
