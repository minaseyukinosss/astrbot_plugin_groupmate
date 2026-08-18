"""Shadow composition service for actor, cognition, and governance routing."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from .attention import AttentionFrame
from .cognition.contracts import CognitiveContext, CognitiveWorker
from .cognition.service import CognitionBudget, CognitionService
from .contracts import RuntimeMode, SocialEventEnvelope
from .event_fabric import SocialEventFabric
from .governor import GovernorContext, GovernorResult, SocialGovernor
from .intentions import IntentionEngine
from .persistence.event_store import AppendResult, SQLiteSocialEventStore
from .persistence.repositories import SQLitePersonaStateRepository
from .scene_actor import GroupSceneActor, SceneWorkRequest, SceneWorkResult
from .supervisor import PersonaSupervisor


class ShadowSideEffectForbidden(RuntimeError):
    """Raised when a Shadow runtime attempts an external side effect."""


class PhaseARuntimeModeError(RuntimeError):
    """Raised before I/O when a mode is not available during Phase A."""


@dataclass(frozen=True)
class ShadowEvaluation:
    request_id: str
    frame: AttentionFrame
    governor_result: GovernorResult
    accepted: bool
    status: str


class NoSideEffectExecutionPort:
    """Fail-closed execution port installed for Phase A Shadow operation."""

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
        config_version: int = 1,
        event_store: SQLiteSocialEventStore | None = None,
        cognition_workers: Mapping[str, CognitiveWorker] | None = None,
        cognition_budget: CognitionBudget | None = None,
    ) -> None:
        resolved_mode = RuntimeMode(mode)
        if resolved_mode is not RuntimeMode.SHADOW:
            raise PhaseARuntimeModeError("Phase A manager only supports SHADOW")
        self.persona_id = persona_id
        self.mode = resolved_mode
        self.enabled_groups = frozenset(map(str, enabled_groups))
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
        self.fabric = SocialEventFabric(self._new_actor)
        self._started = False
        self._startup_requests = ()

    async def start(self) -> None:
        if not self._started:
            await self.supervisor.start()
            for group_id in self.event_store.pending_groups(self.persona_id):
                if group_id in self.enabled_groups:
                    await self.fabric.notify(self.persona_id, group_id)
            self._started = True
            self._startup_requests = await self.fabric.drain()

    async def ingest(self, envelope: SocialEventEnvelope) -> AppendResult | None:
        if self.mode is RuntimeMode.OFF:
            return None
        if envelope.persona_id != self.persona_id:
            raise ValueError("event persona does not match manager")
        if not envelope.group_id or envelope.group_id not in self.enabled_groups:
            return None
        appended = self.event_store.append(envelope)
        if appended.inserted:
            await self.fabric.notify(envelope.persona_id, envelope.group_id)
        return appended

    async def drain(self, *, now: int | None = None) -> tuple[ShadowEvaluation, ...]:
        recovered = self._startup_requests
        self._startup_requests = ()
        requests = recovered + await self.fabric.drain()
        if now is not None:
            flushed = await self.fabric.flush_attention(now)
            requests += flushed
        evaluations = []
        for request in requests:
            for frame in request.attention_frames:
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

    async def group_snapshot(self, group_id: str):
        return await (await self.fabric.notify(self.persona_id, group_id)).snapshot()

    async def close(self) -> None:
        if self._started:
            await self.fabric.close()
            await self.supervisor.close()
            self._started = False

    def _new_actor(self, persona_id: str, group_id: str) -> GroupSceneActor:
        async def snapshot_provider():
            return await self.supervisor.snapshot(self.config_version)

        return GroupSceneActor(
            persona_id,
            group_id,
            self.event_store,
            snapshot_provider,
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
                    frame.focus_event_ids
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
                privacy_allowed=True,
                boundary_active=False,
                paused=False,
                platform_available=True,
                capability_allowed=False,
                force_observe=blackboard.degraded,
                rate_limited_until=None,
                minimum_utility=1.0,
            ),
        )
        result = SceneWorkResult(
            request_id=request.request_id,
            group_id=request.group_id,
            scene_version=frame.scene_version,
            observations=(),
            config_version=frame.config_version,
            persona_state_version=frame.persona_state_version,
            frame_id=frame.frame_id,
            governor_result=asdict(governor_result),
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


__all__ = (
    "NoSideEffectExecutionPort",
    "PhaseARuntimeModeError",
    "ShadowEvaluation",
    "ShadowSideEffectForbidden",
    "SocialRuntimeManager",
)
