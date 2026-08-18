"""Single-writer actor for a persona's authoritative global self state."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Union

from .contracts import GlobalSelfState, GlobalStateEffect, PersonaSnapshot
from .persistence.repositories import (
    SQLitePersonaStateRepository,
    StateVersionConflict,
)


class SupervisorNotRunning(RuntimeError):
    """Raised when a command targets an inactive supervisor."""


@dataclass(frozen=True)
class _SnapshotCommand:
    config_version: int
    future: asyncio.Future[PersonaSnapshot]


@dataclass(frozen=True)
class _EffectCommand:
    effect: GlobalStateEffect
    future: asyncio.Future[PersonaSnapshot]


@dataclass(frozen=True)
class _StopCommand:
    future: asyncio.Future[None]


_Command = Union[_SnapshotCommand, _EffectCommand, _StopCommand]


class PersonaSupervisor:
    """Serializes all reads and writes through one asynchronous mailbox."""

    def __init__(
        self, persona_id: str, repository: SQLitePersonaStateRepository
    ) -> None:
        if not persona_id.strip():
            raise ValueError("persona_id must not be empty")
        self.persona_id = persona_id
        self._repository = repository
        self._mailbox: asyncio.Queue[_Command] = asyncio.Queue()
        self._state: GlobalSelfState | None = None
        self._actor_task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._actor_task is not None and not self._actor_task.done():
                return
            self._state = self._repository.load(self.persona_id)
            self._actor_task = asyncio.create_task(
                self._run(), name=f"persona-supervisor:{self.persona_id}"
            )

    async def snapshot(self, config_version: int) -> PersonaSnapshot:
        if config_version < 0:
            raise ValueError("config_version must not be negative")
        future = self._command_future()
        await self._submit(_SnapshotCommand(config_version, future))
        return await future

    async def apply_effect(self, effect: GlobalStateEffect) -> PersonaSnapshot:
        future = self._command_future()
        await self._submit(_EffectCommand(effect, future))
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
            future = self._command_future(result_type=None)
            await self._mailbox.put(_StopCommand(future))
            await future
            await task
            self._actor_task = None

    async def _submit(self, command: _Command) -> None:
        task = self._actor_task
        if task is None or task.done():
            raise SupervisorNotRunning("PersonaSupervisor is not running")
        await self._mailbox.put(command)

    def _command_future(self, result_type=PersonaSnapshot):
        del result_type
        return asyncio.get_running_loop().create_future()

    async def _run(self) -> None:
        while True:
            command = await self._mailbox.get()
            try:
                if isinstance(command, _StopCommand):
                    command.future.set_result(None)
                    return
                if isinstance(command, _SnapshotCommand):
                    command.future.set_result(
                        self._snapshot_for(command.config_version)
                    )
                    continue

                applied = self._repository.apply_effect(
                    self.persona_id, command.effect
                )
                if self._state is None or applied.version >= self._state.version:
                    self._state = applied
                command.future.set_result(self._to_snapshot(applied, config_version=0))
            except BaseException as exc:
                if not command.future.done():
                    command.future.set_exception(exc)
            finally:
                self._mailbox.task_done()

    def _snapshot_for(self, config_version: int) -> PersonaSnapshot:
        if self._state is None:
            raise SupervisorNotRunning("PersonaSupervisor state is not loaded")
        return self._to_snapshot(self._state, config_version)

    @staticmethod
    def _to_snapshot(
        state: GlobalSelfState, config_version: int
    ) -> PersonaSnapshot:
        return PersonaSnapshot(
            persona_id=state.persona_id,
            state_version=state.version,
            config_version=config_version,
            presence=state.presence,
            energy=state.energy,
            mode="social",
            modifiers=(),
            valence=state.valence,
            arousal=state.arousal,
            irritation=state.irritation,
            cognitive_load=state.cognitive_load,
            recovery_state=state.recovery_state,
            last_transition_at=state.last_transition_at,
            next_transition_at=state.next_transition_at,
        )


__all__ = ("PersonaSupervisor", "StateVersionConflict", "SupervisorNotRunning")
