"""Lifecycle registry for group scene actors."""

from __future__ import annotations

import asyncio
from typing import Callable

from .scene_actor import GroupSceneActor, SceneWorkRequest


class EventFabricClosed(RuntimeError):
    """Raised when routing is attempted after fabric shutdown starts."""


class SocialEventFabric:
    def __init__(self, actor_factory: Callable[[str, str], GroupSceneActor]) -> None:
        self._actor_factory = actor_factory
        self._actors: dict[tuple[str, str], GroupSceneActor] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def actors(self) -> tuple[GroupSceneActor, ...]:
        return tuple(self._actors.values())

    async def open(self) -> None:
        async with self._lock:
            self._closed = False

    async def notify(self, persona_id: str, group_id: str | None) -> GroupSceneActor:
        if not group_id:
            raise ValueError("group events require group_id")
        key = (persona_id, group_id)
        async with self._lock:
            if self._closed:
                raise EventFabricClosed("social event fabric is closed")
            actor = self._actors.get(key)
            if actor is None:
                actor = self._actor_factory(*key)
                await actor.start()
                self._actors[key] = actor
            return actor

    async def drain(self) -> tuple[SceneWorkRequest, ...]:
        batches = await asyncio.gather(*(actor.drain() for actor in self.actors))
        return tuple(item for batch in batches for item in batch)

    async def flush_attention(self, now: int) -> tuple[SceneWorkRequest, ...]:
        batches = await asyncio.gather(
            *(actor.flush_attention(now) for actor in self.actors)
        )
        return tuple(item for batch in batches for item in batch)

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            actors = tuple(self._actors.values())
            self._actors.clear()
        await asyncio.gather(*(actor.close() for actor in actors))


__all__ = ("EventFabricClosed", "SocialEventFabric")
