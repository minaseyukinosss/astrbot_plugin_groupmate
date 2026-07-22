"""Explicit cognitive workflow for deciding and producing group-chat replies."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import DefaultDict, Deque, List, Optional
from uuid import uuid4

from .delivery import build_delivery_plan, delivery_still_valid
from .guardrails import AemeathOutputGuard
from .models import (
    Decision,
    DecisionAction,
    GroupPolicy,
    MemoryItem,
    MemoryKind,
    ReplyPlan,
    TopicSnapshot,
    TriggerKind,
    Urgency,
    WorkflowOutcome,
)
from .ports import (
    Clock,
    DecisionModelPort,
    GenerationModelPort,
    MemoryRepository,
    PersonaProvider,
    PlatformPort,
    TraceSink,
    VisionPort,
)
from .rate_limit import SlidingWindowRateLimiter


class CognitiveWorkflow:
    def __init__(
        self,
        decision_model: DecisionModelPort,
        generation_model: GenerationModelPort,
        vision: VisionPort,
        platform: PlatformPort,
        memory: MemoryRepository,
        persona: PersonaProvider,
        output_guard: AemeathOutputGuard,
        rate_limiter: SlidingWindowRateLimiter,
        clock: Clock,
        trace: Optional[TraceSink] = None,
    ) -> None:
        self.decision_model = decision_model
        self.generation_model = generation_model
        self.vision = vision
        self.platform = platform
        self.memory = memory
        self.persona = persona
        self.output_guard = output_guard
        self.rate_limiter = rate_limiter
        self.clock = clock
        self.trace = trace
        self._recent_outputs: DefaultDict[str, Deque[str]] = defaultdict(
            lambda: deque(maxlen=20)
        )

    async def evaluate(
        self,
        topic: TopicSnapshot,
        trigger: TriggerKind,
        policy: GroupPolicy,
    ) -> WorkflowOutcome:
        decision_id = uuid4().hex
        now = self.clock.now()
        self._record(decision_id, topic.group_id, "OBSERVE", trigger.value, now)

        if not topic.messages:
            return self._silent(decision_id, topic.group_id, "empty_topic", now)
        if trigger in (TriggerKind.IGNORE, TriggerKind.COMMAND):
            return self._silent(decision_id, topic.group_id, "bypassed_trigger", now)
        if trigger is TriggerKind.NATIVE_DIRECT and not policy.handle_native_wake:
            return self._silent(decision_id, topic.group_id, "bypassed_trigger", now)
        if trigger not in (
            TriggerKind.ALIAS_DIRECT,
            TriggerKind.NATIVE_DIRECT,
            TriggerKind.CONTINUATION,
        ) and now - topic.updated_at > policy.candidate_ttl_seconds:
            return self._silent(decision_id, topic.group_id, "stale_topic", now)
        if trigger is TriggerKind.CANDIDATE and not self.rate_limiter.allow(now):
            return self._silent(decision_id, topic.group_id, "rate_limited", now)

        query = " ".join(message.text for message in topic.messages[-8:] if message.text)
        subject_ids = tuple(
            dict.fromkeys(
                message.sender_id
                for message in topic.messages[-8:]
                if message.sender_id and not message.is_bot
            )
        )
        search_kwargs = {
            "group_id": topic.group_id,
            "query": query,
            "now": now,
            "limit": 8,
        }
        try:
            memories = list(
                self.memory.search_memories(subject_ids=subject_ids, **search_kwargs)
            )
        except TypeError:
            memories = list(self.memory.search_memories(**search_kwargs))
        self._record(decision_id, topic.group_id, "RECALL", str(len(memories)), now)

        if trigger in (
            TriggerKind.ALIAS_DIRECT,
            TriggerKind.NATIVE_DIRECT,
            TriggerKind.CONTINUATION,
        ):
            if trigger is TriggerKind.NATIVE_DIRECT:
                reason_code = "native_direct"
                contribution = "回应对方刚才的直接呼叫"
            elif trigger is TriggerKind.CONTINUATION:
                reason_code = "conversation_continuation"
                contribution = "继续回应对方刚才的对话"
            else:
                reason_code = "alias_direct"
                contribution = "回应对方刚才的直接呼叫"
            decision = Decision.respond(
                contribution=contribution,
                confidence=1.0,
                trigger=trigger,
                reason_code=reason_code,
                target_message_id=topic.latest.message_id if topic.latest else None,
                urgency=Urgency.HIGH,
            )
        else:
            try:
                decision = await self.decision_model.decide(topic, policy, memories)
            except Exception:
                return self._silent(decision_id, topic.group_id, "decision_error", now)

        if not isinstance(decision, Decision):
            return self._silent(decision_id, topic.group_id, "invalid_decision", now)
        self._record(
            decision_id,
            topic.group_id,
            "GATE",
            decision.reason_code or decision.action.value,
            now,
        )
        if decision.action is not DecisionAction.RESPOND:
            return self._silent(decision_id, topic.group_id, "decision_ignore", now)
        if trigger not in (
            TriggerKind.ALIAS_DIRECT,
            TriggerKind.NATIVE_DIRECT,
            TriggerKind.CONTINUATION,
        ) and decision.confidence < policy.decision_threshold:
            return self._silent(decision_id, topic.group_id, "below_threshold", now)

        memories = await self._add_visual_context(
            decision_id, topic, decision, policy, memories, now
        )
        persona_prompt = await self.persona.system_prompt(topic.group_id)
        plan = ReplyPlan(
            decision_id=decision_id,
            group_id=topic.group_id,
            trigger=trigger,
            contribution=decision.contribution,
            target_message_id=decision.target_message_id,
            urgency=decision.urgency,
            persona_prompt=persona_prompt,
            image_urls=self._topic_image_urls(topic) if decision.needs_vision else (),
        )
        self._record(decision_id, topic.group_id, "PLAN", decision.contribution, now)

        try:
            text = await self.generation_model.generate(plan, topic, memories)
        except Exception:
            return self._silent(decision_id, topic.group_id, "generation_error", now)

        guarded = self.output_guard.validate(
            text,
            recent_outputs=tuple(self._recent_outputs[topic.group_id]),
        )
        if not guarded.accepted and guarded.repairable:
            try:
                repaired = await self.generation_model.repair(text, guarded.codes)
            except Exception:
                return self._silent(decision_id, topic.group_id, "repair_error", now)
            guarded = self.output_guard.validate(
                repaired,
                recent_outputs=tuple(self._recent_outputs[topic.group_id]),
            )
        if not guarded.accepted:
            return self._silent(
                decision_id,
                topic.group_id,
                "guard_rejected:" + ",".join(guarded.codes),
                now,
            )
        self._record(decision_id, topic.group_id, "GUARD", "accepted", now)

        direct_wake = trigger in (
            TriggerKind.ALIAS_DIRECT,
            TriggerKind.NATIVE_DIRECT,
            TriggerKind.CONTINUATION,
        )
        delivery = build_delivery_plan(
            decision_id=decision_id,
            group_id=topic.group_id,
            text=guarded.text,
            urgency=decision.urgency,
            now=now,
            ttl_seconds=policy.candidate_ttl_seconds,
            max_chars=policy.max_reply_chars,
            max_segments=policy.max_reply_segments,
            humanize_delay=policy.humanize_delay_enabled,
            direct_wake=direct_wake,
            quote_message_id=decision.target_message_id,
        )
        if not delivery.segments:
            return self._silent(decision_id, topic.group_id, "empty_delivery", now)
        self._record(
            decision_id,
            topic.group_id,
            "SCHEDULE",
            "delay={:.2f};segments={}".format(
                delivery.delay_seconds, len(delivery.segments)
            ),
            now,
        )

        outbox_text = "\n".join(delivery.segments)
        enqueue = getattr(self.memory, "enqueue_outbox", None)
        if enqueue and not enqueue(
            decision_id,
            topic.group_id,
            outbox_text,
            created_at=now,
            expires_at=delivery.expires_at,
        ):
            return self._silent(decision_id, topic.group_id, "duplicate_outbox", now)

        if delivery.delay_seconds > 0:
            await asyncio.sleep(delivery.delay_seconds)
        send_now = self.clock.now()
        if not delivery_still_valid(delivery, send_now):
            return self._silent(decision_id, topic.group_id, "delivery_expired", send_now)

        try:
            await self._send_delivery(delivery)
        except Exception as exc:
            return self._silent(
                decision_id,
                topic.group_id,
                "send_error:" + exc.__class__.__name__ + ":" + str(exc),
                send_now,
            )

        mark_sent = getattr(self.memory, "mark_outbox_sent", None)
        if mark_sent:
            mark_sent(decision_id, sent_at=send_now)
        if trigger is TriggerKind.CANDIDATE:
            self.rate_limiter.record(send_now)
        self._recent_outputs[topic.group_id].append(outbox_text)
        self._record(decision_id, topic.group_id, "SEND", "sent", send_now)

        return WorkflowOutcome(
            decision_id=decision_id,
            sent=True,
            reason="sent",
            text=outbox_text,
        )

    async def _send_delivery(self, delivery) -> None:
        sender = getattr(self.platform, "send_segments", None)
        if sender is not None:
            await sender(
                delivery.group_id,
                delivery.segments,
                delivery.decision_id,
                delivery.quote_message_id,
            )
            return
        for segment in delivery.segments:
            await self.platform.send_text(
                delivery.group_id, segment, delivery.decision_id
            )

    async def _add_visual_context(
        self,
        decision_id: str,
        topic: TopicSnapshot,
        decision: Decision,
        policy: GroupPolicy,
        memories: List[MemoryItem],
        now: int,
    ) -> List[MemoryItem]:
        if not decision.needs_vision or not policy.vision_enabled:
            return memories
        image_urls = self._topic_image_urls(topic)
        if not image_urls:
            return memories
        try:
            description = (await self.vision.describe(image_urls)).strip()
        except Exception:
            description = ""
        if description:
            memories.append(
                MemoryItem(
                    memory_id=decision_id + "-vision",
                    group_id=topic.group_id,
                    subject_id="group",
                    kind=MemoryKind.EPISODIC,
                    text="本轮图片内容：" + description,
                    created_at=now,
                    expires_at=now + 60,
                    confidence=0.8,
                    importance=0.5,
                )
            )
            self._record(decision_id, topic.group_id, "VISION", "described", now)
        return memories

    @staticmethod
    def _topic_image_urls(topic: TopicSnapshot) -> tuple:
        urls = []
        for message in topic.messages[-8:]:
            urls.extend(message.image_urls)
        return tuple(dict.fromkeys(urls))

    def _silent(
        self,
        decision_id: str,
        group_id: str,
        reason: str,
        now: int,
    ) -> WorkflowOutcome:
        self._record(decision_id, group_id, "END", reason, now)
        return WorkflowOutcome(decision_id=decision_id, sent=False, reason=reason)

    def _record(
        self,
        decision_id: str,
        group_id: str,
        state: str,
        reason: str,
        timestamp: int,
    ) -> None:
        sink = self.trace
        if sink is not None:
            sink.record(decision_id, group_id, state, reason, timestamp)
            return
        record = getattr(self.memory, "record_transition", None)
        if record:
            record(decision_id, group_id, state, reason, timestamp)
