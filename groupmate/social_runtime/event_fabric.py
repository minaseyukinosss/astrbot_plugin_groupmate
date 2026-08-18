"""Lifecycle registry for group scene actors."""

from __future__ import annotations

import asyncio
from typing import Callable

from .scene_actor import GroupSceneActor, SceneWorkRequest


class SocialEventFabric:
    def __init__(self, actor_factory: Callable[[str, str], GroupSceneActor]) -> None:
        self._actor_factory = actor_factory
        self._actors: dict[tuple[str, str], GroupSceneActor] = {}
        self._lock = asyncio.Lock()

    @property
    def actors(self) -> tuple[GroupSceneActor, ...]:
        return tuple(self._actors.values())

    async def notify(self, persona_id: str, group_id: str | None) -> GroupSceneActor:
        if not group_id:
            raise ValueError("group events require group_id")
        key = (persona_id, group_id)
        async with self._lock:
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
        await asyncio.gather(*(actor.close() for actor in self.actors))
        self._actors.clear()


__all__ = ("SocialEventFabric",)
