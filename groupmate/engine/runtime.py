"""Serialized per-group actor runtime and debounce scheduling."""

from __future__ import annotations

import asyncio
import inspect
import random
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import Callable, Deque, Dict, Optional, Set, Tuple
from uuid import uuid4

from ..models import (
    ChatMessage,
    MessageOrigin,
    TriggerKind,
    WorkflowOutcome,
)
from ..persona.registry import PersonaContext
from ..policies import BehaviorPolicy
from ..core.scenes import classify_scene, is_hard_scene
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
class _ApplyOutcome:
    generation: int
    trigger: TriggerKind
    topic: object
    outcome: WorkflowOutcome


@dataclass(frozen=True)
class _QueuedHardTurn:
    trigger: TriggerKind
    alias: str
    topic: object


@dataclass(frozen=True)
class _Stop:
    pass


class GroupActor:
    def __init__(
        self,
        group_id: str,
        workflow,
        persona_context: PersonaContext,
        behavior: BehaviorPolicy,
        random_source: Optional[random.Random] = None,
    ) -> None:
        self.group_id = str(group_id)
        self.workflow = workflow
        self.persona_context = persona_context
        self.behavior = behavior
        self.router = TriggerRouter(persona_context.aliases)
        self.window = TopicWindow(
            self.group_id,
            max_messages=behavior.conversation.history_limit,
        )
        self._queue = asyncio.Queue()
        self._worker = None
        self._debounce_task = None
        self._evaluation_tasks: Set[asyncio.Task] = set()
        self._soft_task = None
        self._hard_task = None
        self._deferred_message = None
        self._hard_queue: Deque[_QueuedHardTurn] = deque()
        self._generation = 0
        self._random = random_source or random.Random()
        self._closed = False
        self._dispatch_enabled = True
        self.last_trigger = TriggerKind.IGNORE
        self.last_outcome = None
        self._continuations: Dict[str, int] = {}

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())

    async def submit(self, message: ChatMessage, schedule: bool = True) -> None:
        if self._closed:
            raise RuntimeError("group actor is closed")
        if self._worker is None:
            await self.start()
        await self._queue.put(
            _Ingest(self._stamp_message(message, schedule=schedule), schedule=schedule)
        )

    async def preload(self, message: ChatMessage) -> None:
        if self._closed:
            raise RuntimeError("group actor is closed")
        if self._worker is None:
            await self.start()
        await self._queue.put(
            _Ingest(
                self._stamp_message(message, schedule=False),
                schedule=False,
            )
        )

    async def drain(self) -> None:
        while True:
            await self._queue.join()
            task = self._debounce_task
            if task is not None and not task.done():
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            tasks = tuple(task for task in self._evaluation_tasks if not task.done())
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                continue
            await self._queue.join()
            if (
                self._debounce_task is not None
                and not self._debounce_task.done()
            ) or any(not task.done() for task in self._evaluation_tasks):
                continue
            break
        await self.workflow.memory.flush_async()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._dispatch_enabled = False
        self._generation += 1
        self._cancel_debounce()
        for task in tuple(self._evaluation_tasks):
            if not task.done():
                task.cancel()
        if self._evaluation_tasks:
            await asyncio.gather(*tuple(self._evaluation_tasks), return_exceptions=True)
        self._hard_queue.clear()
        await self._close_open_epoch("SHUTDOWN")
        if self._worker is not None:
            await self._queue.put(_Stop())
            await self._queue.join()
            await self._worker
            self._worker = None

    def snapshot(self) -> dict:
        outcome = self.last_outcome
        payload = {
            "persona_id": self.persona_context.persona_id,
            "group_id": self.group_id,
            "messages": len(self.window.snapshot().messages),
            "pending": bool(self._debounce_task and not self._debounce_task.done()),
            "in_flight": sum(not task.done() for task in self._evaluation_tasks),
            "pending_hard": len(self._hard_queue),
            "dispatch_enabled": self._dispatch_enabled,
            "last_trigger": self.last_trigger.value,
            "closed": self._closed,
            "continuation_active": bool(self._continuations),
            "continuation_senders": len(self._continuations),
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
                elif isinstance(item, _ApplyOutcome):
                    await self._apply_outcome(item)
            finally:
                self._queue.task_done()

    async def _handle_ingest(self, item: _Ingest) -> None:
        message = item.message
        classified = self.router.classify(message)
        if classified.kind is TriggerKind.COMMAND:
            self.last_trigger = classified.kind
            return

        appended = self.window.append(message)
        if appended:
            await self.workflow.memory.save_message_async(
                self.persona_context.persona_id,
                message,
            )
        if not item.schedule or not self._dispatch_enabled:
            return

        result = self._maybe_continue(message, classified)
        if not appended:
            if result.kind not in (
                TriggerKind.NATIVE_DIRECT,
                TriggerKind.ALIAS_DIRECT,
                TriggerKind.COPIED_AT,
                TriggerKind.CONTINUATION,
                TriggerKind.HOST_INTERACTION,
            ):
                return

        self.last_trigger = result.kind
        if result.kind in (TriggerKind.IGNORE, TriggerKind.COMMAND):
            return
        scene = classify_scene(result.kind, message)
        if is_hard_scene(scene, result.kind):
            await self._evaluate_immediate(result.kind, result.alias)
            return
        if self._hard_task is not None:
            # Soft traffic is intentionally coalesced while a hard turn is active.
            self._deferred_message = message
            return

        self._generation += 1
        self._cancel_debounce()
        self._cancel_soft_task()
        generation = self._generation
        delay = self._random.uniform(
            self.behavior.conversation.debounce_min_seconds,
            self.behavior.conversation.debounce_max_seconds,
        )
        topic = self.window.snapshot()
        if topic.created_at:
            elapsed = max(0, int(message.timestamp) - int(topic.created_at))
            remaining = max(
                0.0,
                float(self.behavior.conversation.topic_max_seconds) - float(elapsed),
            )
            delay = min(float(delay), remaining)
        self._debounce_task = asyncio.create_task(
            self._enqueue_evaluation(generation, result.kind, delay)
        )

    async def _evaluate_immediate(self, trigger: TriggerKind, alias: str = "") -> None:
        self._cancel_debounce()
        self._cancel_soft_task()
        topic = self.window.snapshot()
        if self._hard_task is not None:
            self._hard_queue.append(_QueuedHardTurn(trigger, alias, topic))
            return
        self._generation += 1
        generation = self._generation
        self._launch_evaluation(generation, topic, trigger, alias, soft=False)

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
        self._launch_evaluation(
            item.generation, topic, item.trigger, "", soft=True
        )

    def set_dispatch_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._dispatch_enabled == enabled:
            return
        self._dispatch_enabled = enabled
        if not enabled:
            self._generation += 1
            self._cancel_debounce()
            for task in tuple(self._evaluation_tasks):
                if not task.done():
                    task.cancel()
            self._soft_task = None
            self._hard_task = None
            self._deferred_message = None
            self._hard_queue.clear()

    def _launch_evaluation(
        self,
        generation: int,
        topic,
        trigger: TriggerKind,
        alias: str,
        *,
        soft: bool,
    ) -> None:
        task = asyncio.create_task(
            self._run_evaluation(generation, topic, trigger, alias)
        )
        self._evaluation_tasks.add(task)
        if soft:
            self._soft_task = task
        else:
            self._hard_task = task
        task.add_done_callback(self._evaluation_done)

    async def _run_evaluation(
        self, generation: int, topic, trigger: TriggerKind, alias: str
    ) -> None:
        if generation != self._generation or not self._dispatch_enabled:
            return
        evaluate = self.workflow.evaluate
        parameters = inspect.signature(evaluate).parameters
        kwargs = {"trigger_alias": alias} if alias else {}
        if "still_valid" in parameters:
            kwargs["still_valid"] = lambda: (
                generation == self._generation and self._dispatch_enabled
            )
        try:
            outcome = await evaluate(topic, trigger, self.behavior, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception:
            outcome = WorkflowOutcome(
                decision_id="runtime-error-{}".format(generation),
                sent=False,
                reason="workflow_error",
            )
        await self._queue.put(_ApplyOutcome(generation, trigger, topic, outcome))

    def _evaluation_done(self, task: asyncio.Task) -> None:
        self._evaluation_tasks.discard(task)
        if self._soft_task is task:
            self._soft_task = None
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass

    async def _apply_outcome(self, item: _ApplyOutcome) -> None:
        if item.generation != self._generation:
            return
        self.last_trigger = item.trigger
        self.last_outcome = item.outcome
        if self._hard_task is not None and item.trigger not in (
            TriggerKind.ALIAS_MENTION,
            TriggerKind.CANDIDATE,
        ):
            self._hard_task = None
        await self._remember_continuation(item.outcome, item.topic, item.trigger)
        if item.outcome.sent and item.outcome.text:
            self._append_bot_projection(item.outcome)
        close_reason = (
            "HARD_WAKE"
            if item.trigger
            in (
                TriggerKind.NATIVE_DIRECT,
                TriggerKind.ALIAS_DIRECT,
                TriggerKind.COPIED_AT,
                TriggerKind.CONTINUATION,
                TriggerKind.HOST_INTERACTION,
            )
            else "EVALUATED"
        )
        await self._rotate_topic_epoch(close_reason)
        if self._hard_queue and self._dispatch_enabled:
            queued = self._hard_queue.popleft()
            self._generation += 1
            self._launch_evaluation(
                self._generation,
                queued.topic,
                queued.trigger,
                queued.alias,
                soft=False,
            )
            return
        deferred = self._deferred_message
        self._deferred_message = None
        if deferred is not None and self._dispatch_enabled:
            result = self._maybe_continue(deferred, self.router.classify(deferred))
            self.last_trigger = result.kind
            if result.kind is TriggerKind.CONTINUATION:
                self._generation += 1
                self._launch_evaluation(
                    self._generation,
                    self.window.snapshot(),
                    result.kind,
                    result.alias,
                    soft=False,
                )
            elif result.kind not in (TriggerKind.IGNORE, TriggerKind.COMMAND):
                self._generation += 1
                generation = self._generation
                delay = self._random.uniform(
                    self.behavior.conversation.debounce_min_seconds,
                    self.behavior.conversation.debounce_max_seconds,
                )
                self._debounce_task = asyncio.create_task(
                    self._enqueue_evaluation(generation, result.kind, delay)
                )

    def _maybe_continue(
        self, message: ChatMessage, result: TriggerResult
    ) -> TriggerResult:
        if result.kind is not TriggerKind.CANDIDATE:
            return result
        if self.behavior.conversation.continuation_seconds <= 0:
            return result
        expires_at = self._continuations.get(message.sender_id)
        if expires_at is None:
            return result
        if message.timestamp > expires_at:
            self._continuations.pop(message.sender_id, None)
            return result
        return TriggerResult(TriggerKind.CONTINUATION, "conversation_continuation")

    async def _remember_continuation(
        self,
        outcome: Optional[WorkflowOutcome],
        topic,
        trigger: TriggerKind,
    ) -> None:
        if outcome is None or not outcome.sent:
            return
        # Only hard direct wakes open a grant; continuation replies never renew.
        if trigger not in (TriggerKind.ALIAS_DIRECT, TriggerKind.NATIVE_DIRECT):
            return
        latest = topic.latest
        if latest is None or latest.is_bot:
            return
        seconds = int(self.behavior.conversation.continuation_seconds)
        if seconds <= 0:
            self._clear_continuation()
            return
        granted_at = int(latest.timestamp)
        expires_at = granted_at + seconds
        self._continuations[latest.sender_id] = expires_at
        await self.workflow.memory.grant_continuation_async(
            persona_id=self.persona_context.persona_id,
            grant_id=uuid4().hex,
            group_id=self.group_id,
            sender_id=latest.sender_id,
            opened_by_decision_id=outcome.decision_id,
            opened_by_message_id=latest.message_id,
            trigger_kind=trigger.name,
            granted_at=granted_at,
            expires_at=expires_at,
            max_total_seconds=seconds,
        )

    def _append_bot_projection(self, outcome: WorkflowOutcome) -> None:
        topic = self.window.snapshot()
        latest = topic.latest
        stamp = int(latest.timestamp) + 1 if latest else 0
        character = self.workflow.character_name
        bot_message = ChatMessage(
            message_id="bot-" + outcome.decision_id,
            group_id=self.group_id,
            sender_id="__bot__",
            sender_name=character,
            text=outcome.text,
            timestamp=stamp,
            is_bot=True,
            segment_types=("text",),
            origin=MessageOrigin.BOT_DELIVERY,
            decision_id=outcome.decision_id,
            ingested_at=stamp,
            metadata={
                "origin": "bot_delivery",
                "decision_id": outcome.decision_id,
            },
        )
        self.window.append(bot_message)

    def set_continuation(self, sender_id: str, expires_at: int) -> None:
        sender_id = str(sender_id or "")
        expires_at = int(expires_at or 0)
        if not sender_id:
            self._continuations.clear()
        elif expires_at > 0:
            self._continuations[sender_id] = expires_at
        else:
            self._continuations.pop(sender_id, None)

    def _clear_continuation(self) -> None:
        self._continuations.clear()

    def _stamp_message(self, message: ChatMessage, *, schedule: bool) -> ChatMessage:
        if message.origin in (
            MessageOrigin.BOT_DELIVERY,
            MessageOrigin.SYSTEM_SYNTHETIC,
        ):
            ingested_at = int(message.ingested_at or time.time())
            return (
                message
                if message.ingested_at == ingested_at
                else replace(message, ingested_at=ingested_at)
            )
        origin = (
            MessageOrigin.PLATFORM_REALTIME
            if schedule
            else MessageOrigin.PLATFORM_HISTORY
        )
        ingested_at = int(message.ingested_at or 0)
        if ingested_at <= 0:
            ingested_at = int(time.time())
        if message.origin is origin and message.ingested_at == ingested_at:
            return message
        return replace(message, origin=origin, ingested_at=ingested_at)

    async def _rotate_topic_epoch(self, close_reason: str) -> None:
        topic = self.window.snapshot()
        now = int(time.time())
        last_id = topic.latest.message_id if topic.latest else None
        await self.workflow.memory.close_topic_epoch_async(
            self.persona_context.persona_id,
            self.group_id,
            topic.topic_id,
            now,
            close_reason,
            last_id,
        )
        new_topic_id = self.window.reset_topic()
        await self.workflow.memory.open_topic_epoch_async(
            self.persona_context.persona_id,
            self.group_id,
            new_topic_id,
            now,
            last_id,
            close_existing_reason=close_reason,
        )

    async def _close_open_epoch(self, close_reason: str) -> None:
        epoch = self.workflow.memory.latest_open_topic_epoch(
            self.persona_context.persona_id,
            self.group_id,
        )
        if not epoch:
            return
        topic = self.window.snapshot()
        last_id = topic.latest.message_id if topic.latest else epoch.get("last_message_id")
        await self.workflow.memory.close_topic_epoch_async(
            self.persona_context.persona_id,
            self.group_id,
            epoch["topic_id"],
            int(time.time()),
            close_reason,
            last_id,
        )

    def _cancel_debounce(self) -> None:
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()
        self._debounce_task = None

    def _cancel_soft_task(self) -> None:
        if self._soft_task is not None and not self._soft_task.done():
            self._soft_task.cancel()
        self._soft_task = None


class GroupRuntimeManager:
    def __init__(
        self,
        workflow_factory: Callable[[str, PersonaContext], object],
        persona_factory: Callable[[str], PersonaContext],
        behavior_factory: Callable[[str], BehaviorPolicy],
    ) -> None:
        self.workflow_factory = workflow_factory
        self.persona_factory = persona_factory
        self.behavior_factory = behavior_factory
        self._dispatch_enabled = True
        self._actors: Dict[Tuple[str, str], GroupActor] = {}
        # Constructed by AstrBot during plugin loading, which is not guaranteed
        # to have a current loop on Python 3.7. Bind the lock lazily in actor_for.
        self._lock = None

    async def submit(self, message: ChatMessage, schedule: bool = True) -> None:
        actor = await self.actor_for(
            message.group_id,
            self.persona_factory(message.group_id),
        )
        await actor.submit(message, schedule=schedule)

    async def preload(self, message: ChatMessage) -> None:
        actor = await self.actor_for(
            message.group_id,
            self.persona_factory(message.group_id),
        )
        await actor.preload(message)

    async def actor_for(
        self,
        group_id: str,
        persona_context: PersonaContext,
    ) -> GroupActor:
        group_id = str(group_id)
        key = (persona_context.persona_id, group_id)
        if key in self._actors:
            return self._actors[key]
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if key not in self._actors:
                actor = GroupActor(
                    group_id,
                    self.workflow_factory(group_id, persona_context),
                    persona_context,
                    self.behavior_factory(group_id),
                )
                actor.set_dispatch_enabled(self._dispatch_enabled)
                await actor.start()
                self._actors[key] = actor
        return self._actors[key]

    async def drain(self) -> None:
        await asyncio.gather(*(actor.drain() for actor in self._actors.values()))

    async def close(self) -> None:
        await asyncio.gather(*(actor.close() for actor in self._actors.values()))
        self._actors.clear()

    def set_dispatch_enabled(self, enabled: bool) -> None:
        self._dispatch_enabled = bool(enabled)
        for actor in self._actors.values():
            actor.set_dispatch_enabled(self._dispatch_enabled)

    def snapshots(self, persona_id: str) -> Dict[str, dict]:
        persona_id = str(persona_id or "").strip()
        if not persona_id:
            raise ValueError("persona_id must not be empty")
        return {
            group_id: actor.snapshot()
            for (actor_persona_id, group_id), actor in self._actors.items()
            if actor_persona_id == persona_id
        }
