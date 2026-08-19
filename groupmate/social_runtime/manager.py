"""Shadow composition service for actor, cognition, and governance routing."""

from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from .attention import AttentionFrame
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
from .governor import GovernorContext, GovernorResult, SocialGovernor
from .intentions import IntentionEngine
from .persistence.event_store import AppendResult, SQLiteSocialEventStore
from .persistence.schema import connect_database
from .persistence.repositories import SQLitePersonaStateRepository
from .delivery.outbox import OutboxService
from .scene_actor import GroupSceneActor, SceneWorkRequest, SceneWorkResult
from .supervisor import PersonaSupervisor
from .tasks.runtime import TaskRuntime


class ShadowSideEffectForbidden(RuntimeError):
    """Raised when a Shadow runtime attempts an external side effect."""


class RuntimeModeUnavailable(RuntimeError):
    """Raised before I/O when a runtime mode has not passed its release gate."""


@dataclass(frozen=True)
class ShadowEvaluation:
    request_id: str
    frame: AttentionFrame
    governor_result: GovernorResult
    accepted: bool
    status: str


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
        governance_state: RuntimeGovernanceState | None = None,
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
        self.event_store = event_store or SQLiteSocialEventStore(database_path)
        self.supervisor = PersonaSupervisor(
            persona_id, SQLitePersonaStateRepository(database_path)
        )
        self.execution_port = NoSideEffectExecutionPort()
        self.cognition = CognitionService(
            workers=cognition_workers or {},
            budget=cognition_budget or CognitionBudget(8, 12),
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
            if now is not None:
                flushed = await self.fabric.flush_attention(now)
                requests += flushed
            evaluations = []
            for request in requests:
                for frame in request.attention_frames:
                    if frame.frame_id in request.evaluated_frame_ids:
                        continue
                    evaluations.append(
                        await self._evaluate_cycle(
                            request,
                            frame,
                            now=max(frame.deadline, request.event.received_at)
                            if now is None
                            else int(now),
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
            return await self.supervisor.snapshot(self.config_version)

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
    ) -> ShadowEvaluation:
        context = CognitiveContext.create(
            group_id=request.group_id,
            scene_version=frame.scene_version,
            persona_state_version=frame.persona_state_version,
            config_version=frame.config_version,
            now=now,
            focus_events=tuple(
                event.to_dict()
                for event in self.event_store.event_envelopes(
                    request.persona_id,
                    request.group_id,
                    frame.focus_event_ids,
                )
            ),
            world_summary=asdict(request.world_snapshot),
            constraints=("shadow_only", "no_side_effects", "evidence_required"),
            token_budget=1024,
        )
        blackboard = await self.cognition.evaluate(frame, context)
        candidates = self.intentions.propose(blackboard, now)
        governor_result = self.governor.decide(
            candidates,
            GovernorContext(
                now=now,
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
        result = SceneWorkResult(
            request_id=request.request_id,
            group_id=request.group_id,
            scene_version=frame.scene_version,
            config_version=frame.config_version,
            persona_state_version=frame.persona_state_version,
            frame_id=frame.frame_id,
            governor_result=governor_result,
        )
        actor = await self.fabric.notify(request.persona_id, request.group_id)
        accepted = await actor.accept_result(result)
        return ShadowEvaluation(
            request_id=request.request_id,
            frame=frame,
            governor_result=governor_result,
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
    "RuntimeGovernanceState",
    "RuntimeModeUnavailable",
    "ShadowEvaluation",
    "ShadowSideEffectForbidden",
    "SocialRuntimeManager",
)
