"""Explicit cognitive workflow for deciding and producing group-chat replies."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import DefaultDict, Deque, List, Optional, Sequence
from uuid import uuid4

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
        if trigger in (TriggerKind.IGNORE, TriggerKind.COMMAND, TriggerKind.NATIVE_DIRECT):
            return self._silent(decision_id, topic.group_id, "bypassed_trigger", now)
        if now - topic.updated_at > policy.candidate_ttl_seconds:
            return self._silent(decision_id, topic.group_id, "stale_topic", now)
        if trigger is TriggerKind.CANDIDATE and not self.rate_limiter.allow(now):
            return self._silent(decision_id, topic.group_id, "rate_limited", now)

        query = " ".join(message.text for message in topic.messages[-8:] if message.text)
        memories = list(
            self.memory.search_memories(
                topic.group_id,
                query,
                now=now,
                limit=8,
            )
        )
        self._record(decision_id, topic.group_id, "RECALL", str(len(memories)), now)

        if trigger is TriggerKind.ALIAS_DIRECT:
            decision = Decision.respond(
                contribution="回应对方刚才的直接呼叫",
                confidence=1.0,
                trigger=trigger,
                reason_code="alias_direct",
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
        if trigger is not TriggerKind.ALIAS_DIRECT and decision.confidence < policy.decision_threshold:
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

        enqueue = getattr(self.memory, "enqueue_outbox", None)
        if enqueue and not enqueue(
            decision_id,
            topic.group_id,
            guarded.text,
            created_at=now,
            expires_at=now + policy.candidate_ttl_seconds,
        ):
            return self._silent(decision_id, topic.group_id, "duplicate_outbox", now)
        try:
            await self.platform.send_text(topic.group_id, guarded.text, decision_id)
        except Exception:
            return self._silent(decision_id, topic.group_id, "send_error", now)

        mark_sent = getattr(self.memory, "mark_outbox_sent", None)
        if mark_sent:
            mark_sent(decision_id, sent_at=now)
        if trigger is TriggerKind.CANDIDATE:
            self.rate_limiter.record(now)
        self._recent_outputs[topic.group_id].append(guarded.text)
        self._record(decision_id, topic.group_id, "SEND", "sent", now)
        return WorkflowOutcome(
            decision_id=decision_id,
            sent=True,
            reason="sent",
            text=guarded.text,
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

