"""Shadow composition service for actor, cognition, and governance routing."""

from __future__ import annotations

import asyncio
import time
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable, Mapping

from .attention import AttentionFrame, ambient_deadline_expired
from .actions.contracts import ActionPlan, DeliveryBundle, PlanValidation
from .actions.coordinator import ExecutionCoordinator
from .cognition.contracts import CognitiveContext, CognitiveWorker
from .cognition.service import CognitionBudget, CognitionService
from .contracts import (
    RuntimeGovernanceState,
    RuntimeMode,
    SocialEventEnvelope,
)
from .event_fabric import SocialEventFabric
from .governor import (
    GovernorContext,
    GovernorResult,
    RejectedIntention,
    SocialGovernor,
)
from .intentions import CandidateIntention, IntentionEngine
from .persistence.event_store import AppendResult, SQLiteSocialEventStore
from .persistence.schema import connect_database
from .persistence.repositories import SQLitePersonaStateRepository
from .persona.profile import GroupmatePersonaProfile
from .delivery.outbox import OutboxService
from .scene_actor import GroupSceneActor, SceneWorkRequest, SceneWorkResult
from .supervisor import PersonaSupervisor
from .tasks.runtime import TaskRuntime


class ShadowSideEffectForbidden(RuntimeError):
    """Raised when a Shadow runtime attempts an external side effect."""


class RuntimeModeUnavailable(RuntimeError):
    """Raised before I/O when a runtime mode has not passed its release gate."""


@dataclass(frozen=True)
class _PersonaProfileSnapshot:
    version: int
    profile: GroupmatePersonaProfile


@dataclass(frozen=True)
class ShadowEvaluation:
    persona_id: str
    request_id: str
    runtime_mode: RuntimeMode
    scene_version: int
    config_version: int
    frame: AttentionFrame | None
    governor_result: GovernorResult
    source_event: SocialEventEnvelope
    context_events: tuple[SocialEventEnvelope, ...]
    candidates: tuple[CandidateIntention, ...]
    accepted: bool
    status: str

    def to_capture_evidence(self) -> dict[str, object]:
        frame_id = (
            self.frame.frame_id
            if self.frame is not None
            else f"external:{self.request_id}"
        )
        return {
            "capture_id": f"runtime-shadow:{self.persona_id}:{frame_id}",
            "evaluation": {
                "persona_id": self.persona_id,
                "request_id": self.request_id,
                "runtime_mode": self.runtime_mode.value,
                "scene_version": self.scene_version,
                "config_version": self.config_version,
                "frame": asdict(self.frame) if self.frame is not None else None,
                "governor_result": asdict(self.governor_result),
                "source_event": self.source_event.to_dict(),
                "context_events": [
                    event.to_dict() for event in self.context_events
                ],
                "candidates": [asdict(candidate) for candidate in self.candidates],
                "accepted": self.accepted,
                "status": self.status,
            },
        }

    @classmethod
    def from_capture_evidence(
        cls, evidence: Mapping[str, object]
    ) -> "ShadowEvaluation":
        values = dict(evidence.get("evaluation") or {})
        frame_values = values.get("frame")
        if frame_values is not None:
            frame_values = dict(frame_values)
            for key in (
                "focus_topic_ids",
                "focus_event_ids",
                "candidate_audiences",
                "requested_workers",
            ):
                frame_values[key] = tuple(frame_values.get(key, ()))
        governor_values = dict(values["governor_result"])
        governor_values["selected_intention_ids"] = tuple(
            governor_values.get("selected_intention_ids", ())
        )
        governor_values["rejected"] = tuple(
            RejectedIntention(
                intention_id=str(item["intention_id"]),
                reason_codes=tuple(item.get("reason_codes", ())),
            )
            for item in governor_values.get("rejected", ())
        )
        governor_values["reason_codes"] = tuple(
            governor_values.get("reason_codes", ())
        )
        governor_values["constraints"] = tuple(
            governor_values.get("constraints", ())
        )
        candidates = []
        for item in values.get("candidates", ()):
            candidate = dict(item)
            candidate["evidence_event_ids"] = tuple(
                candidate.get("evidence_event_ids", ())
            )
            candidates.append(CandidateIntention(**candidate))
        return cls(
            persona_id=str(values["persona_id"]),
            request_id=str(values["request_id"]),
            runtime_mode=RuntimeMode(values["runtime_mode"]),
            scene_version=int(values["scene_version"]),
            config_version=int(values["config_version"]),
            frame=None if frame_values is None else AttentionFrame(**frame_values),
            governor_result=GovernorResult(**governor_values),
            source_event=SocialEventEnvelope.from_dict(values["source_event"]),
            context_events=tuple(
                SocialEventEnvelope.from_dict(item)
                for item in values.get("context_events", ())
            ),
            candidates=tuple(candidates),
            accepted=bool(values["accepted"]),
            status=str(values["status"]),
        )


@dataclass(frozen=True)
class PendingShadowReviewEvidence:
    capture_id: str
    evaluation: ShadowEvaluation


class NoSideEffectExecutionPort:
    """Fail-closed execution port installed for Shadow operation."""

    def __init__(self) -> None:
        self._calls: list[object] = []

    @property
    def calls(self) -> tuple[object, ...]:
        return tuple(self._calls)

    async def execute(self, action: object) -> None:
        self._calls.append(action)
        raise ShadowSideEffectForbidden("external side effects are disabled")


class SocialRuntimeManager:
    def __init__(
        self,
        *,
        database_path: Path,
        persona_id: str,
        mode: RuntimeMode,
        enabled_groups: tuple[str, ...],
        social_runtime_test_groups: tuple[str, ...] = (),
        config_version: int = 1,
        event_store: SQLiteSocialEventStore | None = None,
        cognition_workers: Mapping[str, CognitiveWorker] | None = None,
        cognition_budget: CognitionBudget | None = None,
        worker_concurrency_limit: int = 12,
        governance_state: RuntimeGovernanceState | None = None,
        persona_profile_loader: Callable[[str], object] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        resolved_mode = RuntimeMode(mode)
        enabled = frozenset(
            str(group_id).strip()
            for group_id in enabled_groups
            if str(group_id).strip()
        )
        test_groups = frozenset(
            str(group_id).strip()
            for group_id in social_runtime_test_groups
            if str(group_id).strip()
        )
        if resolved_mode is RuntimeMode.OFF:
            raise RuntimeModeUnavailable(
                "current release gate only supports SHADOW or allowlisted SOCIAL_RUNTIME"
            )
        if resolved_mode is RuntimeMode.SOCIAL_RUNTIME and (
            not test_groups or not test_groups.issubset(enabled)
        ):
            raise RuntimeModeUnavailable(
                "current release gate only supports SHADOW unless Gate C has a "
                "test group allowlist contained in enabled_groups"
            )
        self.persona_id = persona_id
        self.mode = resolved_mode
        self.enabled_groups = enabled
        self.social_runtime_test_groups = test_groups
        self.config_version = config_version
        self._persona_profile_loader = persona_profile_loader
        self._persona_profiles: dict[
            tuple[str, int], GroupmatePersonaProfile
        ] = {}
        resolved_clock = time.time if clock is None else clock
        if not callable(resolved_clock):
            raise ValueError("runtime clock must be callable")
        self._clock = resolved_clock
        self.event_store = event_store or SQLiteSocialEventStore(database_path)
        self.supervisor = PersonaSupervisor(
            persona_id, SQLitePersonaStateRepository(database_path)
        )
        self.execution_port = NoSideEffectExecutionPort()
        self.cognition = CognitionService(
            workers=cognition_workers or {},
            budget=cognition_budget
            or CognitionBudget(
                8,
                12,
                max_worker_concurrency=int(worker_concurrency_limit),
            ),
        )
        self.intentions = IntentionEngine()
        self.governor = SocialGovernor()
        self._governance_state = governance_state or RuntimeGovernanceState()
        self.fabric = SocialEventFabric(self._new_actor, self.event_store)
        self.task_runtime = TaskRuntime(database_path)
        self.outbox = OutboxService(
            database_path,
            group_authorizer=lambda group_id: self.group_mode(group_id)
            is RuntimeMode.SOCIAL_RUNTIME,
            bundle_authorizer=self._bundle_has_matching_plan,
        )
        self.coordinator = ExecutionCoordinator(
            database_path,
            task_runtime=self.task_runtime,
            outbox=self.outbox,
            event_sink=self._publish_execution_event,
            plan_authorizer=lambda plan: self.require_social_runtime_group(
                plan.group_id
            ),
        )
        self._started = False
        self._closing = False
        self._active_drains = 0
        self._lifecycle_lock = asyncio.Lock()
        self._drains_idle = asyncio.Event()
        self._drains_idle.set()
        self._closed = asyncio.Event()
        self._closed.set()
        self._startup_requests = ()
        self._expired_attention_count = 0

    async def start(self) -> None:
        while True:
            initialize_feedback = False
            async with self._lifecycle_lock:
                if self._closing:
                    closed = self._closed
                else:
                    if self._started:
                        return
                    await self.fabric.open()
                    await self.supervisor.start()
                    for group_id in self.event_store.pending_groups(self.persona_id):
                        if group_id in self.enabled_groups:
                            await self.fabric.notify(self.persona_id, group_id)
                    self._started = True
                    self._closed.set()
                    initialize_feedback = True
            if initialize_feedback:
                try:
                    await self.coordinator.recover_feedback()
                    startup_requests = await self.fabric.drain()
                except BaseException:
                    await self.close()
                    raise
                async with self._lifecycle_lock:
                    self._startup_requests = startup_requests
                return
            await closed.wait()

    async def ingest(self, envelope: SocialEventEnvelope) -> AppendResult | None:
        async with self._lifecycle_lock:
            self._ensure_available()
            if envelope.persona_id != self.persona_id:
                raise ValueError("event persona does not match manager")
            if not envelope.group_id or envelope.group_id not in self.enabled_groups:
                return None
            return await self.fabric.publish(envelope)

    async def _publish_execution_event(
        self, envelope: SocialEventEnvelope
    ) -> AppendResult:
        async with self._lifecycle_lock:
            self._ensure_available()
            if envelope.persona_id != self.persona_id:
                raise ValueError("execution feedback persona does not match manager")
            if not envelope.group_id or envelope.group_id not in self.enabled_groups:
                raise ValueError("execution feedback requires an enabled group")
            return await self.fabric.publish(envelope)

    async def drain(self, *, now: int | None = None) -> tuple[ShadowEvaluation, ...]:
        await self._begin_drain()
        try:
            recovered = self._startup_requests
            self._startup_requests = ()
            requests = recovered + await self.fabric.drain()
            flushed = await self.fabric.flush_attention(self._resolve_now(now))
            requests += flushed
            evaluations = []
            for request in requests:
                if self._is_external_compatibility_request(request):
                    evaluations.append(
                        await self._external_compatibility_cycle(request)
                    )
                    continue
                for frame in request.attention_frames:
                    if frame.frame_id in request.evaluated_frame_ids:
                        continue
                    cycle_now = self._resolve_now(now)
                    if ambient_deadline_expired(frame, cycle_now):
                        actor = await self.fabric.notify(
                            request.persona_id, request.group_id
                        )
                        discarded = await actor.discard_work(
                            request.request_id,
                            "attention_deadline_expired",
                        )
                        self._expired_attention_count += int(discarded)
                        continue
                    evaluations.append(
                        await self._evaluate_cycle(
                            request,
                            frame,
                            now=cycle_now,
                            explicit_now=now is not None,
                        )
                    )
            return tuple(evaluations)
        finally:
            await self._end_drain()

    async def group_snapshot(self, group_id: str):
        await self._begin_drain()
        try:
            actor = await self.fabric.notify(self.persona_id, group_id)
            return await actor.snapshot()
        finally:
            await self._end_drain()

    @property
    def governance_state(self) -> RuntimeGovernanceState:
        return self._governance_state

    @property
    def expired_attention_count(self) -> int:
        return self._expired_attention_count

    def group_mode(self, group_id: str) -> RuntimeMode:
        normalized = str(group_id).strip()
        if normalized not in self.enabled_groups:
            return RuntimeMode.OFF
        if (
            self.mode is RuntimeMode.SOCIAL_RUNTIME
            and normalized in self.social_runtime_test_groups
        ):
            return RuntimeMode.SOCIAL_RUNTIME
        return RuntimeMode.SHADOW

    def require_social_runtime_group(self, group_id: str) -> None:
        if self.group_mode(group_id) is not RuntimeMode.SOCIAL_RUNTIME:
            raise RuntimeModeUnavailable(
                "Gate C external actions require an explicit test group allowlist"
            )

    def pending_shadow_review_evidence(
        self,
    ) -> tuple[PendingShadowReviewEvidence, ...]:
        return tuple(
            PendingShadowReviewEvidence(
                capture_id=item.capture_id,
                evaluation=ShadowEvaluation.from_capture_evidence(item.payload),
            )
            for item in self.event_store.pending_shadow_captures(
                self.persona_id, tuple(sorted(self.enabled_groups))
            )
        )

    def complete_shadow_review_evidence(self, capture_id: str) -> bool:
        return self.event_store.complete_shadow_capture(capture_id)

    def submit_plan(
        self,
        plan: ActionPlan,
        validation: PlanValidation,
        *,
        now: int,
    ):
        self.require_social_runtime_group(plan.group_id)
        return self.coordinator.submit(plan, validation, now=now)

    def _bundle_has_matching_plan(self, bundle: DeliveryBundle) -> bool:
        if not hasattr(self, "coordinator"):
            return False
        with closing(connect_database(self.outbox.path)) as db:
            rows = db.execute(
                "SELECT plan_id FROM action_plans WHERE correlation_id=? "
                "AND persona_id=? AND group_id=? "
                "AND status IN ('running','completed')",
                (bundle.correlation_id, bundle.persona_id, bundle.group_id),
            ).fetchall()
        for row in rows:
            plan = self.coordinator.load(str(row["plan_id"])).plan
            if (
                bundle.topic_id == plan.topic_id
                and bundle.expires_at <= plan.expires_at
                and bundle.created_at < bundle.expires_at
            ):
                return True
        return False

    def update_governance_state(
        self,
        state: RuntimeGovernanceState,
        *,
        config_version: int,
    ) -> None:
        if config_version <= self.config_version:
            raise ValueError("governance config version must advance")
        self._governance_state = state
        self.config_version = int(config_version)

    async def close(self) -> None:
        async with self._lifecycle_lock:
            if not self._started:
                return
            if self._closing:
                closed = self._closed
                wait_for_other = True
            else:
                self._closing = True
                self._closed.clear()
                closed = self._closed
                wait_for_other = False
        if wait_for_other:
            await closed.wait()
            return
        await self._drains_idle.wait()
        async with self._lifecycle_lock:
            try:
                await self.fabric.close()
            finally:
                try:
                    await self.supervisor.close()
                finally:
                    self._started = False
                    self._closing = False
                    self._closed.set()

    def _new_actor(self, persona_id: str, group_id: str) -> GroupSceneActor:
        async def snapshot_provider():
            profile = self._load_persona_profile(group_id)
            return await self.supervisor.snapshot(profile.version)

        return GroupSceneActor(
            persona_id,
            group_id,
            self.event_store,
            snapshot_provider,
            lambda: self._governance_state,
        )

    async def _evaluate_cycle(
        self,
        request: SceneWorkRequest,
        frame: AttentionFrame,
        *,
        now: int,
        explicit_now: bool,
    ) -> ShadowEvaluation:
        focus_events = self.event_store.event_envelopes(
            request.persona_id,
            request.group_id,
            frame.focus_event_ids,
        )
        profile = self._persona_profiles.get(
            (request.group_id, frame.config_version)
        )
        if profile is None:
            loaded = self._load_persona_profile(request.group_id)
            if loaded.version != frame.config_version:
                raise RuntimeError("persona profile changed during frozen cognition")
            profile = loaded.profile
        world_summary = asdict(request.world_snapshot)
        world_summary["persona_profile"] = profile.to_mapping()
        context = CognitiveContext.create(
            group_id=request.group_id,
            scene_version=frame.scene_version,
            persona_state_version=frame.persona_state_version,
            config_version=frame.config_version,
            now=now,
            focus_events=tuple(event.to_dict() for event in focus_events),
            world_summary=world_summary,
            constraints=("shadow_only", "no_side_effects", "evidence_required"),
            token_budget=1024,
        )
        blackboard = await self.cognition.evaluate(frame, context)
        decision_now = now if explicit_now else self._resolve_now(None)
        candidates = self.intentions.propose(blackboard, decision_now)
        governor_result = self.governor.decide(
            candidates,
            GovernorContext(
                now=decision_now,
                scene_version=frame.scene_version,
                allowed_target_ids=frame.candidate_audiences,
                allowed_topic_ids=frame.focus_topic_ids,
                privacy_allowed=request.governance_snapshot.privacy_allowed,
                boundary_active=request.event.event_type == "safety.boundary",
                paused=(
                    request.governance_snapshot.paused
                    or request.persona_snapshot.presence in {"paused", "offline"}
                    or request.persona_snapshot.mode == "paused"
                ),
                platform_available=request.governance_snapshot.platform_available,
                capability_allowed=request.governance_snapshot.capability_allowed,
                force_observe=blackboard.degraded,
                rate_limited_until=request.governance_snapshot.rate_limited_until,
                minimum_utility=request.governance_snapshot.minimum_utility,
            ),
        )
        context_events = self.event_store.event_envelopes(
            request.persona_id,
            request.group_id,
            request.world_snapshot.recent_presence.recent_event_ids[-20:],
        )
        evaluation = ShadowEvaluation(
            persona_id=request.persona_id,
            request_id=request.request_id,
            runtime_mode=self.group_mode(request.group_id),
            scene_version=frame.scene_version,
            config_version=frame.config_version,
            frame=frame,
            governor_result=governor_result,
            source_event=request.event,
            context_events=context_events,
            candidates=candidates,
            accepted=True,
            status="accepted",
        )
        result = SceneWorkResult(
            request_id=request.request_id,
            group_id=request.group_id,
            scene_version=frame.scene_version,
            config_version=frame.config_version,
            persona_state_version=frame.persona_state_version,
            frame_id=frame.frame_id,
            governor_result=governor_result,
            capture_evidence=evaluation.to_capture_evidence(),
        )
        actor = await self.fabric.notify(request.persona_id, request.group_id)
        if ambient_deadline_expired(frame, decision_now):
            discarded = await actor.discard_work(
                request.request_id,
                "attention_deadline_expired_after_cognition",
            )
            self._expired_attention_count += int(discarded)
            return replace(evaluation, accepted=False, status="stale")
        accepted = await actor.accept_result(result)
        return replace(
            evaluation,
            accepted=accepted,
            status="accepted" if accepted else "stale",
        )

    def _load_persona_profile(self, group_id: str) -> _PersonaProfileSnapshot:
        if self._persona_profile_loader is None:
            snapshot = _PersonaProfileSnapshot(
                self.config_version,
                GroupmatePersonaProfile.default(),
            )
        else:
            raw = self._persona_profile_loader(group_id)
            version = int(getattr(raw, "version", -1))
            config = getattr(raw, "config", None)
            if version < 0 or not isinstance(config, Mapping):
                raise ValueError("persona profile loader returned an invalid snapshot")
            snapshot = _PersonaProfileSnapshot(
                version,
                (
                    GroupmatePersonaProfile.from_behavior_config(config)
                ),
            )
        self._persona_profiles[(str(group_id), snapshot.version)] = snapshot.profile
        return snapshot

    def _resolve_now(self, explicit: int | None) -> int:
        return int(self._clock()) if explicit is None else int(explicit)

    @staticmethod
    def _is_external_compatibility_request(request: SceneWorkRequest) -> bool:
        return bool(
            request.event.payload.get("interaction_owner") == "EXTERNAL_PLUGIN"
            and request.event.payload.get("social_eligible") is False
            and not request.attention_frames
        )

    async def _external_compatibility_cycle(
        self, request: SceneWorkRequest
    ) -> ShadowEvaluation:
        actor = await self.fabric.notify(request.persona_id, request.group_id)
        context_events = self.event_store.event_envelopes(
            request.persona_id,
            request.group_id,
            request.world_snapshot.recent_presence.recent_event_ids[-20:],
        )
        evaluation = ShadowEvaluation(
            persona_id=request.persona_id,
            request_id=request.request_id,
            runtime_mode=self.group_mode(request.group_id),
            scene_version=request.scene_version,
            config_version=request.persona_snapshot.config_version,
            frame=None,
            governor_result=GovernorResult(
                outcome="SILENCE",
                selected_intention_ids=(),
                rejected=(),
                reason_codes=("external_plugin_owned",),
                reconsider_at=None,
                constraints=("external_plugin_owns_response", "no_side_effects"),
            ),
            source_event=request.event,
            context_events=context_events,
            candidates=(),
            accepted=True,
            status="accepted",
        )
        accepted = await actor.discard_work(
            request.request_id,
            "external_plugin_owned",
            capture_evidence=evaluation.to_capture_evidence(),
        )
        return replace(
            evaluation,
            accepted=accepted,
            status="accepted" if accepted else "stale",
        )

    async def _begin_drain(self) -> None:
        async with self._lifecycle_lock:
            self._ensure_available()
            self._active_drains += 1
            self._drains_idle.clear()

    async def _end_drain(self) -> None:
        async with self._lifecycle_lock:
            self._active_drains -= 1
            if self._active_drains == 0:
                self._drains_idle.set()

    def _ensure_available(self) -> None:
        if not self._started or self._closing:
            raise RuntimeError("social runtime manager is not accepting work")


__all__ = (
    "NoSideEffectExecutionPort",
    "PendingShadowReviewEvidence",
    "RuntimeGovernanceState",
    "RuntimeModeUnavailable",
    "ShadowEvaluation",
    "ShadowSideEffectForbidden",
    "SocialRuntimeManager",
)
