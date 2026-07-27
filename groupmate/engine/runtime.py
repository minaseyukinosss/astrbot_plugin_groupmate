"""Serialized per-group actor runtime and debounce scheduling."""

from __future__ import annotations

import asyncio
import inspect
import random
import time
from dataclasses import dataclass, replace
from typing import Callable, Dict, Optional, Set
from uuid import uuid4

from ..models import (
    ChatMessage,
    GroupPolicy,
    MessageOrigin,
    TriggerKind,
    WorkflowOutcome,
)
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
class _Stop:
    pass


class GroupActor:
    def __init__(
        self,
        group_id: str,
        workflow,
        policy: GroupPolicy,
        random_source: Optional[random.Random] = None,
        v3_scheduler_enabled: bool = True,
    ) -> None:
        self.group_id = str(group_id)
        self.workflow = workflow
        self.policy = policy
        self.router = TriggerRouter(policy)
        self.window = TopicWindow(self.group_id, max_messages=policy.history_limit)
        self._queue = asyncio.Queue()
        self._worker = None
        self._debounce_task = None
        self._evaluation_tasks: Set[asyncio.Task] = set()
        self._soft_task = None
        self._hard_task = None
        self._deferred_message = None
        self._generation = 0
        self._random = random_source or random.Random()
        self._closed = False
        self._dispatch_enabled = True
        self._v3_scheduler_enabled = bool(v3_scheduler_enabled)
        self.last_trigger = TriggerKind.IGNORE
        self.last_outcome = None
        self._continuation_sender_id = ""
        self._continuation_until = 0

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
        flush = getattr(getattr(self.workflow, "memory", None), "flush_async", None)
        if flush is not None:
            await flush()

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
        await self._close_open_epoch("SHUTDOWN")
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
            "in_flight": sum(not task.done() for task in self._evaluation_tasks),
            "dispatch_enabled": self._dispatch_enabled,
            "scheduler": "v3" if self._v3_scheduler_enabled else "legacy",
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
                elif isinstance(item, _ApplyOutcome):
                    await self._apply_outcome(item)
            finally:
                self._queue.task_done()

    async def _handle_ingest(self, item: _Ingest) -> None:
        message = item.message
        appended = self.window.append(message)
        if appended:
            memory = getattr(self.workflow, "memory", None)
            save_async = getattr(memory, "save_message_async", None)
            if save_async is not None:
                await save_async(message)
            else:
                save = getattr(memory, "save_message", None)
                if save:
                    save(message)
        if not item.schedule or not self._dispatch_enabled:
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
        if self._hard_task is not None and result.kind not in (
            TriggerKind.NATIVE_DIRECT,
            TriggerKind.ALIAS_DIRECT,
            TriggerKind.COPIED_AT,
        ):
            self._deferred_message = message
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
        self._cancel_soft_task()
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
        self._cancel_soft_task()
        if trigger is TriggerKind.NATIVE_DIRECT and not self.policy.handle_native_wake:
            return
        generation = self._generation
        topic = self.window.snapshot()
        if not self._v3_scheduler_enabled:
            self.last_outcome = await self.workflow.evaluate(
                topic, trigger, self.policy, trigger_alias=alias
            )
            await self._remember_continuation(self.last_outcome, topic, trigger)
            if self.last_outcome and self.last_outcome.sent and self.last_outcome.text:
                self._append_bot_projection(self.last_outcome)
            await self._rotate_topic_epoch("HARD_WAKE")
            return
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
        if not self._v3_scheduler_enabled:
            self.last_outcome = await self.workflow.evaluate(
                topic, item.trigger, self.policy
            )
            await self._remember_continuation(
                self.last_outcome, topic, item.trigger
            )
            if self.last_outcome and self.last_outcome.sent and self.last_outcome.text:
                self._append_bot_projection(self.last_outcome)
            await self._rotate_topic_epoch("EVALUATED")
            return
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
            outcome = await evaluate(topic, trigger, self.policy, **kwargs)
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
            )
            else "EVALUATED"
        )
        await self._rotate_topic_epoch(close_reason)
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
                    self.policy.debounce_min_seconds,
                    self.policy.debounce_max_seconds,
                )
                self._debounce_task = asyncio.create_task(
                    self._enqueue_evaluation(generation, result.kind, delay)
                )

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
        seconds = int(self.policy.continuation_seconds)
        if seconds <= 0:
            self._clear_continuation()
            return
        granted_at = int(latest.timestamp)
        expires_at = granted_at + seconds
        self._continuation_sender_id = latest.sender_id
        self._continuation_until = expires_at
        memory = getattr(self.workflow, "memory", None)
        grant = getattr(memory, "grant_continuation_async", None)
        if grant is not None:
            await grant(
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
        else:
            grant_sync = getattr(memory, "grant_continuation", None)
            if grant_sync is not None:
                grant_sync(
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
        character = getattr(self.workflow, "character_name", "爱弥斯")
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
        self._continuation_sender_id = str(sender_id or "")
        self._continuation_until = int(expires_at or 0)

    def _clear_continuation(self) -> None:
        self._continuation_sender_id = ""
        self._continuation_until = 0

    def _stamp_message(self, message: ChatMessage, *, schedule: bool) -> ChatMessage:
        if message.origin is MessageOrigin.BOT_DELIVERY:
            return message
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
        memory = getattr(self.workflow, "memory", None)
        topic = self.window.snapshot()
        now = int(time.time())
        last_id = topic.latest.message_id if topic.latest else None
        close = getattr(memory, "close_topic_epoch_async", None)
        if close is not None:
            await close(
                self.group_id,
                topic.topic_id,
                now,
                close_reason,
                last_id,
            )
        else:
            close_sync = getattr(memory, "close_topic_epoch", None)
            if close_sync is not None:
                close_sync(
                    self.group_id, topic.topic_id, now, close_reason, last_id
                )
        new_topic_id = self.window.reset_topic()
        open_epoch = getattr(memory, "open_topic_epoch_async", None)
        if open_epoch is not None:
            await open_epoch(
                self.group_id,
                new_topic_id,
                now,
                last_id,
                close_existing_reason=close_reason,
            )
        else:
            open_sync = getattr(memory, "open_topic_epoch", None)
            if open_sync is not None:
                open_sync(self.group_id, new_topic_id, now, last_id)

    async def _close_open_epoch(self, close_reason: str) -> None:
        memory = getattr(self.workflow, "memory", None)
        latest_open = getattr(memory, "latest_open_topic_epoch", None)
        if latest_open is None:
            return
        epoch = latest_open(self.group_id)
        if not epoch:
            return
        topic = self.window.snapshot()
        last_id = topic.latest.message_id if topic.latest else epoch.get("last_message_id")
        close = getattr(memory, "close_topic_epoch_async", None)
        if close is not None:
            await close(
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
        workflow_factory: Callable[[str], object],
        policy_factory: Callable[[str], GroupPolicy],
        v3_scheduler_enabled: bool = True,
    ) -> None:
        self.workflow_factory = workflow_factory
        self.policy_factory = policy_factory
        self.v3_scheduler_enabled = bool(v3_scheduler_enabled)
        self._dispatch_enabled = True
        self._actors: Dict[str, GroupActor] = {}
        # Constructed by AstrBot during plugin loading, which is not guaranteed
        # to have a current loop on Python 3.7. Bind the lock lazily in actor_for.
        self._lock = None

    async def submit(self, message: ChatMessage, schedule: bool = True) -> None:
        actor = await self.actor_for(message.group_id)
        await actor.submit(message, schedule=schedule)

    async def preload(self, message: ChatMessage) -> None:
        actor = await self.actor_for(message.group_id)
        await actor.preload(message)

    async def actor_for(self, group_id: str) -> GroupActor:
        group_id = str(group_id)
        if group_id in self._actors:
            return self._actors[group_id]
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if group_id not in self._actors:
                actor = GroupActor(
                    group_id,
                    self.workflow_factory(group_id),
                    self.policy_factory(group_id),
                    v3_scheduler_enabled=self.v3_scheduler_enabled,
                )
                actor.set_dispatch_enabled(self._dispatch_enabled)
                await actor.start()
                self._actors[group_id] = actor
        return self._actors[group_id]

    async def drain(self) -> None:
        await asyncio.gather(*(actor.drain() for actor in self._actors.values()))

    async def close(self) -> None:
        await asyncio.gather(*(actor.close() for actor in self._actors.values()))
        self._actors.clear()

    def set_dispatch_enabled(self, enabled: bool) -> None:
        self._dispatch_enabled = bool(enabled)
        for actor in self._actors.values():
            actor.set_dispatch_enabled(self._dispatch_enabled)

    def snapshots(self) -> Dict[str, dict]:
        return {group_id: actor.snapshot() for group_id, actor in self._actors.items()}
