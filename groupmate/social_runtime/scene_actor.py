"""Recoverable single-writer actor for one persona's view of one group."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Awaitable, Callable, Union

from .contracts import PersonaSnapshot, SocialEventEnvelope
from .persistence.event_store import ClaimedEvent, SQLiteSocialEventStore
from .world import GroupWorldProjector, GroupWorldState


class SceneActorNotRunning(RuntimeError):
    """Raised when a command targets an inactive group actor."""


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


@dataclass(frozen=True)
class SceneWorkResult:
    request_id: str
    group_id: str
    scene_version: int
    observations: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class _ProcessCommand:
    claimed: ClaimedEvent
    persona_snapshot: PersonaSnapshot
    future: asyncio.Future[SceneWorkRequest]


@dataclass(frozen=True)
class _SnapshotCommand:
    future: asyncio.Future[GroupWorldState]


@dataclass(frozen=True)
class _AcceptCommand:
    result: SceneWorkResult
    future: asyncio.Future[bool]


@dataclass(frozen=True)
class _StopCommand:
    future: asyncio.Future[None]


_Command = Union[_ProcessCommand, _SnapshotCommand, _AcceptCommand, _StopCommand]
_SnapshotProvider = Callable[[], Awaitable[PersonaSnapshot]]


class GroupSceneActor:
    """Serializes projection while leaving worker execution outside its mailbox."""

    SNAPSHOT_INTERVAL = 100

    def __init__(
        self,
        persona_id: str,
        group_id: str,
        event_store: SQLiteSocialEventStore,
        snapshot_provider: _SnapshotProvider,
    ) -> None:
        if not persona_id.strip() or not group_id.strip():
            raise ValueError("persona_id and group_id must not be empty")
        self.persona_id = persona_id
        self.group_id = group_id
        self.actor_key = f"group:{persona_id}:{group_id}"
        self._store = event_store
        self._snapshot_provider = snapshot_provider
        self._projector = GroupWorldProjector()
        self._state: GroupWorldState | None = None
        self._mailbox: asyncio.Queue[_Command] = asyncio.Queue()
        self._actor_task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._drain_lock = asyncio.Lock()
        self._pending_request_ids: set[str] = set()
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
            self._pending_request_ids = {
                request.request_id for request in self._recovered_requests
            }
            self._actor_task = asyncio.create_task(
                self._run(), name=f"group-scene:{self.persona_id}:{self.group_id}"
            )

    @property
    def pending_request_count(self) -> int:
        return len(self._pending_request_ids)

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
                    future = asyncio.get_running_loop().create_future()
                    await self._mailbox.put(
                        _ProcessCommand(item, persona_snapshot, future)
                    )
                    produced.append(await future)
        current_version = self._require_state().scene_version
        return tuple(
            request for request in produced if request.scene_version == current_version
        )

    async def accept_result(self, result: SceneWorkResult) -> bool:
        self._ensure_running()
        future = asyncio.get_running_loop().create_future()
        await self._mailbox.put(_AcceptCommand(result, future))
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
                if isinstance(command, _AcceptCommand):
                    state = self._require_state()
                    accepted = (
                        command.result.group_id == self.group_id
                        and command.result.scene_version == state.scene_version
                        and command.result.request_id in self._pending_request_ids
                    )
                    persisted = self._store.resolve_scene_work(
                        self.actor_key,
                        command.result.request_id,
                        "accepted" if accepted else "stale",
                    )
                    self._pending_request_ids.discard(command.result.request_id)
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
        state = self._projector.apply(self._require_state(), command.claimed.event)
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
        self._pending_request_ids = {request_id}
        if state.scene_version % self.SNAPSHOT_INTERVAL == 0:
            try:
                self._save_snapshot()
            except Exception:
                # The event, Journal, and Cursor are already committed. A
                # periodic Snapshot is an optimization; recovery can replay
                # committed events and must never downgrade Inbox status.
                self._snapshot_failure_count += 1
        return request

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
        }

    def _request_from_dict(self, payload: dict[str, object]) -> SceneWorkRequest:
        persona_values = dict(payload["persona_snapshot"])
        persona_values["modifiers"] = tuple(persona_values["modifiers"])
        return SceneWorkRequest(
            request_id=str(payload["request_id"]),
            persona_id=str(payload["persona_id"]),
            group_id=str(payload["group_id"]),
            scene_version=int(payload["scene_version"]),
            trigger_event_id=str(payload["trigger_event_id"]),
            event=SocialEventEnvelope.from_dict(payload["event"]),
            world_snapshot=self._projector.from_dict(payload["world_snapshot"]),
            persona_snapshot=PersonaSnapshot(**persona_values),
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
)
