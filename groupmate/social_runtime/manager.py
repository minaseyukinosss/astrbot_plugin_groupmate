"""Phase A composition service for persistence and actor routing."""

from __future__ import annotations

from pathlib import Path

from .contracts import RuntimeMode, SocialEventEnvelope
from .event_fabric import SocialEventFabric
from .persistence.event_store import AppendResult, SQLiteSocialEventStore
from .persistence.repositories import SQLitePersonaStateRepository
from .scene_actor import GroupSceneActor
from .supervisor import PersonaSupervisor


class ShadowSideEffectForbidden(RuntimeError):
    """Raised when a Shadow runtime attempts an external side effect."""


class NoSideEffectExecutionPort:
    """Fail-closed execution port installed for Phase A Shadow operation."""

    @property
    def calls(self) -> tuple[object, ...]:
        return ()

    async def execute(self, action: object) -> None:
        del action
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
    ) -> None:
        self.persona_id = persona_id
        self.mode = RuntimeMode(mode)
        self.enabled_groups = frozenset(map(str, enabled_groups))
        self.config_version = config_version
        self.event_store = event_store or SQLiteSocialEventStore(database_path)
        self.supervisor = PersonaSupervisor(
            persona_id, SQLitePersonaStateRepository(database_path)
        )
        self.execution_port = NoSideEffectExecutionPort()
        self.fabric = SocialEventFabric(self._new_actor)
        self._started = False

    async def start(self) -> None:
        if not self._started:
            await self.supervisor.start()
            for group_id in self.event_store.pending_groups(self.persona_id):
                if group_id in self.enabled_groups:
                    await self.fabric.notify(self.persona_id, group_id)
            self._started = True
            await self.fabric.drain()

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

    async def drain(self):
        return await self.fabric.drain()

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

        return GroupSceneActor(persona_id, group_id, self.event_store, snapshot_provider)


__all__ = (
    "NoSideEffectExecutionPort",
    "ShadowSideEffectForbidden",
    "SocialRuntimeManager",
)
