"""Serialized per-group actor runtime and debounce scheduling."""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from ..models import ChatMessage, GroupPolicy, TriggerKind, WorkflowOutcome
from .topics import TopicWindow
from .triggers import TriggerResult, TriggerRouter


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
        self._continuation_sender_id = ""
        self._continuation_until = 0

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
        outcome = self.last_outcome
        payload = {
            "group_id": self.group_id,
            "messages": len(self.window.snapshot().messages),
            "pending": bool(self._debounce_task and not self._debounce_task.done()),
            "last_trigger": self.last_trigger.value,
            "closed": self._closed,
            "continuation_active": bool(
                self._continuation_sender_id and self._continuation_until > 0
            ),
        }
        if outcome is not None:
            payload["last_outcome"] = {
                "sent": outcome.sent,
                "reason": outcome.reason,
                "text": outcome.text,
            }
        return payload

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
        appended = self.window.append(message)
        if appended:
            save = getattr(getattr(self.workflow, "memory", None), "save_message", None)
            if save:
                save(message)
        if not item.schedule:
            return

        result = self._maybe_continue(message, self.router.classify(message))
        if not appended:
            if result.kind not in (
                TriggerKind.NATIVE_DIRECT,
                TriggerKind.ALIAS_DIRECT,
                TriggerKind.COPIED_AT,
                TriggerKind.CONTINUATION,
            ):
                return

        self.last_trigger = result.kind
        if result.kind in (TriggerKind.IGNORE, TriggerKind.COMMAND):
            return
        if result.kind in (
            TriggerKind.NATIVE_DIRECT,
            TriggerKind.ALIAS_DIRECT,
            TriggerKind.COPIED_AT,
            TriggerKind.CONTINUATION,
        ):
            await self._evaluate_immediate(result.kind, result.alias)
            return

        self._generation += 1
        self._cancel_debounce()
        generation = self._generation
        delay = self._random.uniform(
            self.policy.debounce_min_seconds,
            self.policy.debounce_max_seconds,
        )
        topic = self.window.snapshot()
        if topic.created_at:
            elapsed = max(0, int(message.timestamp) - int(topic.created_at))
            remaining = max(0.0, float(self.policy.topic_max_seconds) - float(elapsed))
            delay = min(float(delay), remaining)
        self._debounce_task = asyncio.create_task(
            self._enqueue_evaluation(generation, result.kind, delay)
        )

    async def _evaluate_immediate(self, trigger: TriggerKind, alias: str = "") -> None:
        self._generation += 1
        self._cancel_debounce()
        if trigger is TriggerKind.NATIVE_DIRECT and not self.policy.handle_native_wake:
            return
        self.last_outcome = await self.workflow.evaluate(
            self.window.snapshot(), trigger, self.policy, trigger_alias=alias
        )
        self._remember_continuation(self.last_outcome)
        self.window.reset_topic()

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
        topic = self.window.snapshot()
        self.last_outcome = await self.workflow.evaluate(
            topic, item.trigger, self.policy
        )
        self._remember_continuation(self.last_outcome)
        self.window.reset_topic()

    def _maybe_continue(
        self, message: ChatMessage, result: TriggerResult
    ) -> TriggerResult:
        if result.kind is not TriggerKind.CANDIDATE:
            return result
        if self.policy.continuation_seconds <= 0:
            return result
        if not self._continuation_sender_id:
            return result
        if message.sender_id != self._continuation_sender_id:
            return result
        if message.timestamp > self._continuation_until:
            self._clear_continuation()
            return result
        return TriggerResult(TriggerKind.CONTINUATION, "conversation_continuation")

    def _remember_continuation(self, outcome: Optional[WorkflowOutcome]) -> None:
        if outcome is None or not outcome.sent:
            return
        if self.last_trigger not in (
            TriggerKind.ALIAS_DIRECT,
            TriggerKind.NATIVE_DIRECT,
            TriggerKind.CONTINUATION,
        ):
            return
        topic = self.window.snapshot()
        latest = topic.latest
        if latest is None or latest.is_bot:
            return
        seconds = int(self.policy.continuation_seconds)
        if seconds <= 0:
            self._clear_continuation()
            return
        self._continuation_sender_id = latest.sender_id
        self._continuation_until = int(latest.timestamp) + seconds
        if outcome.text:
            self._append_bot_reply(outcome)

    def _append_bot_reply(self, outcome: WorkflowOutcome) -> None:
        topic = self.window.snapshot()
        latest = topic.latest
        stamp = int(latest.timestamp) + 1 if latest else 0
        bot_message = ChatMessage(
            message_id="bot-" + outcome.decision_id,
            group_id=self.group_id,
            sender_id="__bot__",
            sender_name="爱弥斯",
            text=outcome.text,
            timestamp=stamp,
            is_bot=True,
            segment_types=("text",),
        )
        if self.window.append(bot_message):
            save = getattr(getattr(self.workflow, "memory", None), "save_message", None)
            if save:
                save(bot_message)

    def _clear_continuation(self) -> None:
        self._continuation_sender_id = ""
        self._continuation_until = 0

    def _cancel_debounce(self) -> None:
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()
        self._debounce_task = None


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
