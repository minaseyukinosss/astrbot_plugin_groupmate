"""Recoverable single-writer actor for one persona's view of one group."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Awaitable, Callable, Union

from .attention import AttentionFrame, AttentionScheduler, PendingAttentionWindow
from .contracts import (
    PersonaSnapshot,
    RuntimeGovernanceState,
    SocialEventEnvelope,
)
from .governor import GovernorResult
from .persistence.event_store import ClaimedEvent, SQLiteSocialEventStore
from .world import GroupWorldProjector, GroupWorldState


class SceneActorNotRunning(RuntimeError):
    """Raised when a command targets an inactive group actor."""


class TaskResultDisposition(str, Enum):
    SEND = "SEND"
    DEFER = "DEFER"
    SILENCE = "SILENCE"


@dataclass(frozen=True)
class TaskResultDecision:
    task_id: str
    disposition: TaskResultDisposition
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class SceneWorkRequest:
    request_id: str
    persona_id: str
    group_id: str
    scene_version: int
    trigger_event_id: str
    event: SocialEventEnvelope
    world_snapshot: GroupWorldState
    persona_snapshot: PersonaSnapshot
    task_result_decision: TaskResultDecision | None = None
    governance_snapshot: RuntimeGovernanceState = RuntimeGovernanceState()
    attention_frames: tuple[AttentionFrame, ...] = ()
    attention_window: PendingAttentionWindow | None = None
    evaluated_frame_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SceneWorkResult:
    request_id: str
    group_id: str
    scene_version: int
    config_version: int
    persona_state_version: int
    frame_id: str
    governor_result: GovernorResult


@dataclass(frozen=True)
class _ProcessCommand:
    claimed: ClaimedEvent
    persona_snapshot: PersonaSnapshot
    governance_snapshot: RuntimeGovernanceState
    future: asyncio.Future[SceneWorkRequest]


@dataclass(frozen=True)
class _SnapshotCommand:
    future: asyncio.Future[GroupWorldState]


@dataclass(frozen=True)
class _AcceptCommand:
    result: SceneWorkResult
    current_persona: PersonaSnapshot
    future: asyncio.Future[bool]


@dataclass(frozen=True)
class _FlushAttentionCommand:
    now: int
    future: asyncio.Future[tuple[SceneWorkRequest, ...]]


@dataclass(frozen=True)
class _DiscardCommand:
    request_id: str
    reason_code: str
    future: asyncio.Future[bool]


@dataclass(frozen=True)
class _StopCommand:
    future: asyncio.Future[None]


_Command = Union[
    _ProcessCommand,
    _SnapshotCommand,
    _AcceptCommand,
    _FlushAttentionCommand,
    _DiscardCommand,
    _StopCommand,
]
_SnapshotProvider = Callable[[], Awaitable[PersonaSnapshot]]
_GovernanceProvider = Callable[[], RuntimeGovernanceState]


class GroupSceneActor:
    """Serializes projection while leaving worker execution outside its mailbox."""

    SNAPSHOT_INTERVAL = 100

    def __init__(
        self,
        persona_id: str,
        group_id: str,
        event_store: SQLiteSocialEventStore,
        snapshot_provider: _SnapshotProvider,
        governance_provider: _GovernanceProvider | None = None,
    ) -> None:
        if not persona_id.strip() or not group_id.strip():
            raise ValueError("persona_id and group_id must not be empty")
        self.persona_id = persona_id
        self.group_id = group_id
        self.actor_key = f"group:{persona_id}:{group_id}"
        self._store = event_store
        self._snapshot_provider = snapshot_provider
        self._governance_provider = governance_provider or RuntimeGovernanceState
        self._projector = GroupWorldProjector()
        self._attention = AttentionScheduler()
        self._state: GroupWorldState | None = None
        self._mailbox: asyncio.Queue[_Command] = asyncio.Queue()
        self._actor_task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._drain_lock = asyncio.Lock()
        self._pending_requests: dict[str, SceneWorkRequest] = {}
        self._recovered_requests: list[SceneWorkRequest] = []
        self._snapshot_failure_count = 0

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._actor_task is not None and not self._actor_task.done():
                return
            self._state = self._recover()
            self._recovered_requests = [
                self._request_from_dict(payload)
                for payload in self._store.pending_scene_work(
                    self.actor_key, self._state.scene_version
                )
            ]
            self._pending_requests = {
                request.request_id: request for request in self._recovered_requests
            }
            for request in self._recovered_requests:
                if request.attention_window is not None:
                    self._attention.restore_window(request.attention_window)
            self._actor_task = asyncio.create_task(
                self._run(), name=f"group-scene:{self.persona_id}:{self.group_id}"
            )

    @property
    def pending_request_count(self) -> int:
        return len(self._pending_requests)

    @property
    def snapshot_failure_count(self) -> int:
        return self._snapshot_failure_count

    async def submit(
        self, event: SocialEventEnvelope
    ) -> SceneWorkRequest | None:
        if event.persona_id != self.persona_id or event.group_id != self.group_id:
            raise ValueError("event does not belong to this group actor")
        self._ensure_running()
        self._store.append(event)
        requests = await self.drain()
        return next(
            (item for item in requests if item.trigger_event_id == event.event_id),
            None,
        )

    async def drain(self) -> tuple[SceneWorkRequest, ...]:
        self._ensure_running()
        produced: list[SceneWorkRequest] = []
        async with self._drain_lock:
            produced.extend(self._recovered_requests)
            self._recovered_requests.clear()
            while True:
                cursor = self._store.cursor(self.actor_key)
                claimed = self._store.claim(
                    self.actor_key,
                    cursor.last_sequence,
                    100,
                    persona_id=self.persona_id,
                    group_id=self.group_id,
                )
                if not claimed:
                    break
                for item in claimed:
                    # Snapshot acquisition may wait on another actor, so it happens
                    # before this actor's mutation command enters the mailbox.
                    persona_snapshot = await self._snapshot_provider()
                    governance_snapshot = self._governance_provider()
                    future = asyncio.get_running_loop().create_future()
                    await self._mailbox.put(
                        _ProcessCommand(
                            item,
                            persona_snapshot,
                            governance_snapshot,
                            future,
                        )
                    )
                    produced.append(await future)
        current_version = self._require_state().scene_version
        return tuple(
            request for request in produced if request.scene_version == current_version
        )

    async def accept_result(self, result: SceneWorkResult) -> bool:
        self._ensure_running()
        current_persona = await self._snapshot_provider()
        future = asyncio.get_running_loop().create_future()
        await self._mailbox.put(_AcceptCommand(result, current_persona, future))
        return await future

    async def flush_attention(self, now: int) -> tuple[SceneWorkRequest, ...]:
        self._ensure_running()
        future = asyncio.get_running_loop().create_future()
        await self._mailbox.put(_FlushAttentionCommand(int(now), future))
        return await future

    async def discard_work(self, request_id: str, reason_code: str) -> bool:
        self._ensure_running()
        if not request_id.strip() or not reason_code.strip():
            raise ValueError("discard identity and reason are required")
        future = asyncio.get_running_loop().create_future()
        await self._mailbox.put(_DiscardCommand(request_id, reason_code, future))
        return await future

    async def snapshot(self) -> GroupWorldState:
        self._ensure_running()
        future = asyncio.get_running_loop().create_future()
        await self._mailbox.put(_SnapshotCommand(future))
        return await future

    async def close(self) -> None:
        async with self._lifecycle_lock:
            task = self._actor_task
            if task is None:
                return
            if task.done():
                self._actor_task = None
                await task
                return
            future = asyncio.get_running_loop().create_future()
            await self._mailbox.put(_StopCommand(future))
            await future
            await task
            self._actor_task = None

    def _recover(self) -> GroupWorldState:
        stored = self._store.load_snapshot(self.actor_key)
        if stored is None:
            state = self._projector.empty(self.group_id)
            snapshot_sequence = 0
        else:
            state = self._projector.from_dict(stored.payload["world"])
            snapshot_sequence = int(stored.payload["last_sequence"])
            if state.group_id != self.group_id:
                raise ValueError("snapshot group does not match actor")

        cursor = self._store.cursor(self.actor_key)
        for committed in self._store.read_events(
            snapshot_sequence,
            cursor.last_sequence,
            persona_id=self.persona_id,
            group_id=self.group_id,
        ):
            state = self._projector.apply(state, committed.event)
        return state

    async def _run(self) -> None:
        while True:
            command = await self._mailbox.get()
            try:
                if isinstance(command, _StopCommand):
                    self._save_snapshot()
                    command.future.set_result(None)
                    return
                if isinstance(command, _SnapshotCommand):
                    command.future.set_result(self._require_state())
                    continue
                if isinstance(command, _FlushAttentionCommand):
                    command.future.set_result(self._flush_attention(command.now))
                    continue
                if isinstance(command, _DiscardCommand):
                    discarded = self._store.resolve_scene_evaluation(
                        self.actor_key,
                        command.request_id,
                        "stale",
                        evaluation=None,
                        resolution={
                            "kind": "explicit_discard",
                            "reason_code": command.reason_code,
                        },
                    )
                    self._pending_requests.pop(command.request_id, None)
                    command.future.set_result(discarded)
                    continue
                if isinstance(command, _AcceptCommand):
                    state = self._require_state()
                    request = self._pending_requests.get(
                        command.result.request_id
                    )
                    if request is None:
                        stored = self._store.scene_work_request(
                            self.actor_key,
                            command.result.request_id,
                        )
                        if stored is not None and stored.status == "accepted":
                            request = self._request_from_dict(stored.payload)
                            persisted = self._store.resolve_scene_evaluation(
                                self.actor_key,
                                command.result.request_id,
                                "accepted",
                                evaluation=self._evaluation_payload(
                                    command.result,
                                    request,
                                ),
                            )
                            command.future.set_result(persisted)
                            continue
                    accepted = bool(
                        request is not None
                        and command.result.group_id == self.group_id
                        and command.result.scene_version == state.scene_version
                        and self._compatible_result(
                            command.result,
                            request,
                            command.current_persona,
                        )
                    )
                    evaluation = (
                        self._evaluation_payload(command.result, request)
                        if accepted and request is not None
                        else None
                    )
                    updated_request = (
                        replace(
                            request,
                            evaluated_frame_ids=self._append_unique(
                                request.evaluated_frame_ids,
                                command.result.frame_id,
                            ),
                        )
                        if accepted and request is not None
                        else None
                    )
                    keep_pending = bool(
                        updated_request is not None
                        and (
                            updated_request.attention_window is not None
                            or self._has_unevaluated_frames(updated_request)
                        )
                    )
                    persisted = self._store.resolve_scene_evaluation(
                        self.actor_key,
                        command.result.request_id,
                        "accepted" if accepted else "stale",
                        evaluation=evaluation,
                        keep_pending_request=(
                            self._request_to_dict(updated_request)
                            if keep_pending and updated_request is not None
                            else None
                        ),
                        resolution=(
                            None
                            if accepted
                            else {
                                "kind": "stale_result",
                                "reason_code": "version_or_scope_mismatch",
                            }
                        ),
                    )
                    if persisted and keep_pending and updated_request is not None:
                        self._pending_requests[command.result.request_id] = (
                            updated_request
                        )
                    else:
                        self._pending_requests.pop(command.result.request_id, None)
                    command.future.set_result(accepted and persisted)
                    continue
                command.future.set_result(self._process(command))
            except BaseException as exc:
                if (
                    isinstance(command, _ProcessCommand)
                    and self._store.cursor(self.actor_key).last_sequence
                    < command.claimed.sequence
                ):
                    self._store.fail(
                        self.actor_key,
                        command.claimed.sequence,
                        type(exc).__name__,
                    )
                if not command.future.done():
                    command.future.set_exception(exc)
                if isinstance(command, _StopCommand):
                    return
            finally:
                self._mailbox.task_done()

    def _process(self, command: _ProcessCommand) -> SceneWorkRequest:
        prior_state = self._require_state()
        task_result_decision = self._task_result_decision(
            command.claimed.event, prior_state
        )
        state = self._projector.apply(prior_state, command.claimed.event)
        attention_frames = self._attention.on_event(
            command.claimed.event,
            state,
            command.persona_snapshot,
            command.claimed.event.received_at,
        )
        request_id = (
            f"scene:{self.persona_id}:{self.group_id}:"
            f"{command.claimed.event.event_id}:{state.scene_version}"
        )
        request = SceneWorkRequest(
            request_id=request_id,
            persona_id=self.persona_id,
            group_id=self.group_id,
            scene_version=state.scene_version,
            trigger_event_id=command.claimed.event.event_id,
            event=command.claimed.event,
            world_snapshot=state,
            persona_snapshot=command.persona_snapshot,
            task_result_decision=task_result_decision,
            governance_snapshot=command.governance_snapshot,
            attention_frames=attention_frames,
            attention_window=self._attention.pending_window(self.group_id),
        )
        self._store.commit(
            self.actor_key,
            command.claimed,
            effects=(
                {
                    "effect_id": (
                        f"world:{self.persona_id}:{self.group_id}:"
                        f"{command.claimed.event.event_id}"
                    ),
                    "kind": "group_world.projected",
                    "scene_version": state.scene_version,
                },
            ),
            work_requests=(
                {
                    "request_id": request_id,
                    "trigger_event_id": command.claimed.event.event_id,
                    "scene_version": state.scene_version,
                    "request": self._request_to_dict(request),
                },
            ),
        )
        self._state = state
        # The transaction has already marked older scene requests stale.
        # Mirror that authoritative state so the actor's cache stays bounded.
        self._pending_requests = {request_id: request}
        if state.scene_version % self.SNAPSHOT_INTERVAL == 0:
            try:
                self._save_snapshot()
            except Exception:
                # The event, Journal, and Cursor are already committed. A
                # periodic Snapshot is an optimization; recovery can replay
                # committed events and must never downgrade Inbox status.
                self._snapshot_failure_count += 1
        return request

    def _flush_attention(self, now: int) -> tuple[SceneWorkRequest, ...]:
        previous_window = self._attention.pending_window(self.group_id)
        frames = self._attention.flush_due(now)
        if not frames or not self._pending_requests:
            return ()
        request = next(reversed(self._pending_requests.values()))
        durable_request = replace(
            request,
            world_snapshot=self._require_state(),
            attention_frames=self._append_frames(
                request.attention_frames,
                frames,
            ),
            attention_window=None,
        )
        if not self._store.refresh_pending_scene_work(
            self.actor_key,
            request.request_id,
            self._request_to_dict(durable_request),
        ):
            if previous_window is not None:
                self._attention.restore_window(previous_window)
            return ()
        self._pending_requests[request.request_id] = durable_request
        dispatch_request = replace(durable_request, attention_frames=frames)
        return (dispatch_request,)

    @staticmethod
    def _compatible_result(
        result: SceneWorkResult,
        request: SceneWorkRequest,
        current_persona: PersonaSnapshot,
    ) -> bool:
        frame_ids = {frame.frame_id for frame in request.attention_frames}
        return bool(
            result.frame_id in frame_ids
            and result.config_version == request.persona_snapshot.config_version
            and result.config_version == current_persona.config_version
            and result.persona_state_version
            == request.persona_snapshot.state_version
            and result.persona_state_version == current_persona.state_version
        )

    @staticmethod
    def _evaluation_payload(
        result: SceneWorkResult, request: SceneWorkRequest
    ) -> dict[str, object]:
        governor = asdict(result.governor_result)
        safe_governor = {
            key: governor[key]
            for key in (
                "outcome",
                "selected_intention_ids",
                "rejected",
                "reason_codes",
                "reconsider_at",
                "constraints",
            )
            if key in governor
        }
        result_id = f"governor:{result.frame_id}"
        return {
            "effect_id": f"shadow:{result.frame_id}",
            "kind": "shadow.governor_evaluated",
            "result_id": result_id,
            "frame_id": result.frame_id,
            "source_event_id": request.trigger_event_id,
            "correlation_id": request.event.correlation_id,
            "causation_id": request.event.causation_id,
            "persona_id": request.persona_id,
            "group_id": request.group_id,
            "scene_version": result.scene_version,
            "config_version": result.config_version,
            "persona_state_version": result.persona_state_version,
            "governor_result": safe_governor,
        }

    def _request_to_dict(self, request: SceneWorkRequest) -> dict[str, object]:
        return {
            "request_id": request.request_id,
            "persona_id": request.persona_id,
            "group_id": request.group_id,
            "scene_version": request.scene_version,
            "trigger_event_id": request.trigger_event_id,
            "event": request.event.to_dict(),
            "world_snapshot": self._projector.to_dict(request.world_snapshot),
            "persona_snapshot": asdict(request.persona_snapshot),
            "task_result_decision": (
                asdict(request.task_result_decision)
                if request.task_result_decision is not None
                else None
            ),
            "governance_snapshot": asdict(request.governance_snapshot),
            "attention_frames": [
                asdict(frame) for frame in request.attention_frames
            ],
            "attention_window": (
                asdict(request.attention_window)
                if request.attention_window is not None
                else None
            ),
            "evaluated_frame_ids": list(request.evaluated_frame_ids),
        }

    def _request_from_dict(self, payload: dict[str, object]) -> SceneWorkRequest:
        persona_values = dict(payload["persona_snapshot"])
        persona_values["modifiers"] = tuple(persona_values["modifiers"])
        task_decision = payload.get("task_result_decision")
        if task_decision is not None:
            task_decision = dict(task_decision)
            task_decision["disposition"] = TaskResultDisposition(
                task_decision["disposition"]
            )
            task_decision["reason_codes"] = tuple(task_decision["reason_codes"])
        return SceneWorkRequest(
            request_id=str(payload["request_id"]),
            persona_id=str(payload["persona_id"]),
            group_id=str(payload["group_id"]),
            scene_version=int(payload["scene_version"]),
            trigger_event_id=str(payload["trigger_event_id"]),
            event=SocialEventEnvelope.from_dict(payload["event"]),
            world_snapshot=self._projector.from_dict(payload["world_snapshot"]),
            persona_snapshot=PersonaSnapshot(**persona_values),
            task_result_decision=(
                None
                if task_decision is None
                else TaskResultDecision(**task_decision)
            ),
            governance_snapshot=RuntimeGovernanceState(
                **payload.get("governance_snapshot", {})
            ),
            attention_frames=tuple(
                self._attention_from_dict(frame)
                for frame in payload.get("attention_frames", ())
            ),
            attention_window=self._attention_window_from_dict(
                payload.get("attention_window")
            ),
            evaluated_frame_ids=tuple(payload.get("evaluated_frame_ids", ())),
        )

    @staticmethod
    def _attention_from_dict(payload: dict[str, object]) -> AttentionFrame:
        values = dict(payload)
        for key in (
            "focus_topic_ids",
            "focus_event_ids",
            "candidate_audiences",
            "requested_workers",
        ):
            values[key] = tuple(values.get(key, ()))
        return AttentionFrame(**values)

    @staticmethod
    def _attention_window_from_dict(
        payload: dict[str, object] | None,
    ) -> PendingAttentionWindow | None:
        if payload is None:
            return None
        values = dict(payload)
        for key in (
            "focus_topic_ids",
            "focus_event_ids",
            "candidate_audiences",
        ):
            values[key] = tuple(values.get(key, ()))
        return PendingAttentionWindow(**values)

    @staticmethod
    def _append_unique(values: tuple[str, ...], value: str) -> tuple[str, ...]:
        return values if value in values else values + (value,)

    @staticmethod
    def _append_frames(
        existing: tuple[AttentionFrame, ...],
        added: tuple[AttentionFrame, ...],
    ) -> tuple[AttentionFrame, ...]:
        known = {frame.frame_id for frame in existing}
        return existing + tuple(frame for frame in added if frame.frame_id not in known)

    @staticmethod
    def _has_unevaluated_frames(request: SceneWorkRequest) -> bool:
        evaluated = set(request.evaluated_frame_ids)
        return any(
            frame.frame_id not in evaluated for frame in request.attention_frames
        )

    @staticmethod
    def _task_result_decision(
        event: SocialEventEnvelope, state: GroupWorldState
    ) -> TaskResultDecision | None:
        if event.event_type not in {"capability.progress", "capability.result"}:
            return None
        task_id = str(event.payload.get("task_id") or "").strip()
        if not task_id:
            return None
        if bool(event.payload.get("direct_request")):
            return TaskResultDecision(
                task_id,
                TaskResultDisposition.SEND,
                ("direct_request_obligation",),
            )
        if not bool(event.payload.get("delivery_relevant", True)):
            return TaskResultDecision(
                task_id,
                TaskResultDisposition.SILENCE,
                ("delivery_no_longer_relevant",),
            )
        topic_id = str(event.payload.get("topic_id") or "").strip()
        active_topic_ids = {topic.topic_id for topic in state.active_topics}
        latest = (
            max(state.active_topics, key=lambda topic: topic.last_event_at).topic_id
            if state.active_topics
            else None
        )
        if topic_id and topic_id == latest:
            return TaskResultDecision(
                task_id,
                TaskResultDisposition.SEND,
                ("task_topic_current",),
            )
        if topic_id and topic_id in active_topic_ids:
            return TaskResultDecision(
                task_id,
                TaskResultDisposition.DEFER,
                ("task_topic_temporarily_displaced",),
            )
        return TaskResultDecision(
            task_id,
            TaskResultDisposition.SILENCE,
            ("task_topic_has_no_social_value",),
        )

    def _save_snapshot(self) -> None:
        state = self._require_state()
        cursor = self._store.cursor(self.actor_key)
        self._store.save_snapshot(
            self.actor_key,
            state.scene_version,
            {
                "last_sequence": cursor.last_sequence,
                "world": self._projector.to_dict(state),
            },
        )

    def _ensure_running(self) -> None:
        if self._actor_task is None or self._actor_task.done():
            raise SceneActorNotRunning("GroupSceneActor is not running")

    def _require_state(self) -> GroupWorldState:
        if self._state is None:
            raise SceneActorNotRunning("GroupSceneActor state is not loaded")
        return self._state


__all__ = (
    "GroupSceneActor",
    "SceneActorNotRunning",
    "SceneWorkRequest",
    "SceneWorkResult",
    "TaskResultDecision",
    "TaskResultDisposition",
)
