"""Serialized per-group actor runtime and debounce scheduling."""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from .models import ChatMessage, GroupPolicy, TriggerKind
from .topics import TopicWindow
from .triggers import TriggerRouter


@dataclass(frozen=True)
class _Ingest:
    message: ChatMessage
    schedule: bool = True


@dataclass(frozen=True)
class _EvaluateTopic:
    generation: int
    trigger: TriggerKind


@dataclass(frozen=True)
class _Stop:
    pass


class GroupActor:
    def __init__(
        self,
        group_id: str,
        workflow,
        policy: GroupPolicy,
        random_source: Optional[random.Random] = None,
    ) -> None:
        self.group_id = str(group_id)
        self.workflow = workflow
        self.policy = policy
        self.router = TriggerRouter(policy)
        self.window = TopicWindow(self.group_id, max_messages=policy.history_limit)
        self._queue = asyncio.Queue()
        self._worker = None
        self._debounce_task = None
        self._generation = 0
        self._random = random_source or random.Random()
        self._closed = False
        self.last_trigger = TriggerKind.IGNORE
        self.last_outcome = None

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())

    async def submit(self, message: ChatMessage) -> None:
        if self._closed:
            raise RuntimeError("group actor is closed")
        if self._worker is None:
            await self.start()
        await self._queue.put(_Ingest(message))

    async def preload(self, message: ChatMessage) -> None:
        if self._closed:
            raise RuntimeError("group actor is closed")
        if self._worker is None:
            await self.start()
        await self._queue.put(_Ingest(message, schedule=False))

    async def drain(self) -> None:
        await self._queue.join()
        task = self._debounce_task
        if task is not None and not task.done():
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._queue.join()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancel_debounce()
        if self._worker is not None:
            await self._queue.put(_Stop())
            await self._queue.join()
            await self._worker
            self._worker = None

    def snapshot(self) -> dict:
        return {
            "group_id": self.group_id,
            "messages": len(self.window.snapshot().messages),
            "pending": bool(self._debounce_task and not self._debounce_task.done()),
            "last_trigger": self.last_trigger.value,
            "closed": self._closed,
        }

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if isinstance(item, _Stop):
                    return
                if isinstance(item, _Ingest):
                    await self._handle_ingest(item)
                elif isinstance(item, _EvaluateTopic):
                    await self._handle_evaluate(item)
            finally:
                self._queue.task_done()

    async def _handle_ingest(self, item: _Ingest) -> None:
        message = item.message
        if not self.window.append(message):
            return
        save = getattr(getattr(self.workflow, "memory", None), "save_message", None)
        if save:
            save(message)
        if not item.schedule:
            return

        result = self.router.classify(message)
        self.last_trigger = result.kind
        if result.kind in (TriggerKind.IGNORE, TriggerKind.COMMAND):
            await self._observe_bypass(result.kind)
            return
        if result.kind is TriggerKind.NATIVE_DIRECT:
            self._generation += 1
            self._cancel_debounce()
            await self._observe_bypass(result.kind)
            self.window.reset_topic()
            return
        if result.kind is TriggerKind.ALIAS_DIRECT:
            self._generation += 1
            self._cancel_debounce()
            self.last_outcome = await self.workflow.evaluate(
                self.window.snapshot(), result.kind, self.policy
            )
            self.window.reset_topic()
            return

        self._generation += 1
        self._cancel_debounce()
        generation = self._generation
        delay = self._random.uniform(
            self.policy.debounce_min_seconds,
            self.policy.debounce_max_seconds,
        )
        self._debounce_task = asyncio.create_task(
            self._enqueue_evaluation(generation, result.kind, delay)
        )

    async def _enqueue_evaluation(
        self,
        generation: int,
        trigger: TriggerKind,
        delay: float,
    ) -> None:
        try:
            await asyncio.sleep(max(0.0, delay))
            await self._queue.put(_EvaluateTopic(generation, trigger))
        except asyncio.CancelledError:
            return

    async def _handle_evaluate(self, item: _EvaluateTopic) -> None:
        if item.generation != self._generation:
            return
        self.last_outcome = await self.workflow.evaluate(
            self.window.snapshot(), item.trigger, self.policy
        )
        self.window.reset_topic()

    def _cancel_debounce(self) -> None:
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()
        self._debounce_task = None

    async def _observe_bypass(self, trigger: TriggerKind) -> None:
        observer = getattr(self.workflow, "observe_bypass", None)
        if observer is None:
            return
        try:
            await observer(self.window.snapshot(), trigger, self.policy)
        except Exception:
            return


class GroupRuntimeManager:
    def __init__(
        self,
        workflow_factory: Callable[[str], object],
        policy_factory: Callable[[str], GroupPolicy],
    ) -> None:
        self.workflow_factory = workflow_factory
        self.policy_factory = policy_factory
        self._actors: Dict[str, GroupActor] = {}
        self._lock = asyncio.Lock()

    async def submit(self, message: ChatMessage) -> None:
        actor = await self.actor_for(message.group_id)
        await actor.submit(message)

    async def preload(self, message: ChatMessage) -> None:
        actor = await self.actor_for(message.group_id)
        await actor.preload(message)

    async def actor_for(self, group_id: str) -> GroupActor:
        group_id = str(group_id)
        if group_id in self._actors:
            return self._actors[group_id]
        async with self._lock:
            if group_id not in self._actors:
                actor = GroupActor(
                    group_id,
                    self.workflow_factory(group_id),
                    self.policy_factory(group_id),
                )
                await actor.start()
                self._actors[group_id] = actor
        return self._actors[group_id]

    async def drain(self) -> None:
        await asyncio.gather(*(actor.drain() for actor in self._actors.values()))

    async def close(self) -> None:
        await asyncio.gather(*(actor.close() for actor in self._actors.values()))
        self._actors.clear()

    def snapshots(self) -> Dict[str, dict]:
        return {group_id: actor.snapshot() for group_id, actor in self._actors.items()}
