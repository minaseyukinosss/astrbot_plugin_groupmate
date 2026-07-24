"""Explicit cognitive workflow for deciding and producing group-chat replies."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import DefaultDict, Deque, List, Optional
from uuid import uuid4

from ..core.session import GroupSession, GroupSessionStore
from ..core.speak_contract import SpeakContract
from ..core.favorability import delta_for_turn, seed_score_for_relationship
from ..core.history_format import focus_speaker
from ..core.relationships import resolve_speaker
from .delivery import build_delivery_plan, delivery_still_valid
from ..models import (
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
from ..ports import (
    Clock,
    GenerationModelPort,
    MemoryRepository,
    OutputGuard,
    PersonaProvider,
    PlatformPort,
    TraceSink,
    VisionPort,
)
from .rate_limit import SlidingWindowRateLimiter
from .topics import select_active_messages

_SOFT_TRIGGERS = frozenset({TriggerKind.ALIAS_MENTION, TriggerKind.CANDIDATE})
_HARD_TRIGGERS = frozenset(
    {
        TriggerKind.ALIAS_DIRECT,
        TriggerKind.NATIVE_DIRECT,
        TriggerKind.CONTINUATION,
    }
)


class CognitiveWorkflow:
    def __init__(
        self,
        generation_model: GenerationModelPort,
        vision: VisionPort,
        platform: PlatformPort,
        memory: MemoryRepository,
        persona: PersonaProvider,
        output_guard: OutputGuard,
        rate_limiter: SlidingWindowRateLimiter,
        clock: Clock,
        trace: Optional[TraceSink] = None,
        sessions: Optional[GroupSessionStore] = None,
        character_name: str = "角色",
    ) -> None:
        self.generation_model = generation_model
        self.vision = vision
        self.platform = platform
        self.memory = memory
        self.persona = persona
        self.output_guard = output_guard
        self.rate_limiter = rate_limiter
        self.clock = clock
        self.trace = trace
        self.character_name = (character_name or "角色").strip() or "角色"
        self.sessions = sessions or GroupSessionStore(
            character_name=self.character_name
        )
        self._recent_outputs: DefaultDict[str, Deque[str]] = defaultdict(
            lambda: deque(maxlen=20)
        )

    def session_for(self, group_id: str) -> GroupSession:
        return self.sessions.get(group_id)

    async def evaluate(
        self,
        topic: TopicSnapshot,
        trigger: TriggerKind,
        policy: GroupPolicy,
        trigger_alias: str = "",
    ) -> WorkflowOutcome:
        decision_id = uuid4().hex
        now = self.clock.now()
        soft_trigger = trigger in _SOFT_TRIGGERS
        self._record(decision_id, topic.group_id, "OBSERVE", trigger.value, now)

        if not topic.messages:
            return self._silent(decision_id, topic.group_id, "empty_topic", now)
        if trigger in (TriggerKind.IGNORE, TriggerKind.COMMAND):
            return self._silent(decision_id, topic.group_id, "bypassed_trigger", now)
        if trigger is TriggerKind.NATIVE_DIRECT and not policy.handle_native_wake:
            return self._silent(decision_id, topic.group_id, "bypassed_trigger", now)
        if trigger is TriggerKind.COPIED_AT:
            return await self._send_copied_at_tip(
                decision_id, topic, trigger_alias, now
            )
        if soft_trigger and now - topic.updated_at > policy.candidate_ttl_seconds:
            return self._silent(decision_id, topic.group_id, "stale_topic", now)
        if trigger is TriggerKind.CANDIDATE and not self.rate_limiter.allow(now):
            return self._silent(decision_id, topic.group_id, "rate_limited", now)

        active = select_active_messages(
            topic.messages, topic_created_at=topic.created_at
        )
        query = " ".join(message.text for message in active if message.text)
        subject_ids = tuple(
            dict.fromkeys(
                message.sender_id
                for message in active
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

        decision = self._build_decision(trigger, topic, soft_trigger)
        self._record(
            decision_id,
            topic.group_id,
            "GATE",
            decision.reason_code or decision.action.value,
            now,
        )
        if decision.action is not DecisionAction.RESPOND:
            return self._silent(decision_id, topic.group_id, "decision_ignore", now)

        memories = await self._add_visual_context(
            decision_id, topic, decision, policy, memories, now
        )
        session = self.session_for(topic.group_id)
        favorability = self._ensure_favorability(topic, now)
        assemble = getattr(self.persona, "assemble", None)
        if assemble is not None:
            try:
                assembled = assemble(
                    topic,
                    memories,
                    contribution=decision.contribution,
                    soft_trigger=soft_trigger,
                    session=session,
                    favorability=favorability,
                )
            except TypeError:
                assembled = assemble(
                    topic,
                    memories,
                    contribution=decision.contribution,
                    soft_trigger=soft_trigger,
                    session=session,
                )
            persona_prompt = assembled.system
            user_prompt = assembled.user
        else:
            persona_prompt = await self.persona.system_prompt(topic.group_id)
            build_user = self.persona.build_user_context
            try:
                user_prompt = build_user(
                    topic,
                    memories,
                    contribution=decision.contribution,
                    soft_trigger=soft_trigger,
                    session=session,
                    favorability=favorability,
                )
            except TypeError:
                try:
                    user_prompt = build_user(
                        topic,
                        memories,
                        contribution=decision.contribution,
                        soft_trigger=soft_trigger,
                        session=session,
                    )
                except TypeError:
                    user_prompt = build_user(topic, memories)

        plan = ReplyPlan(
            decision_id=decision_id,
            group_id=topic.group_id,
            trigger=trigger,
            contribution=decision.contribution,
            target_message_id=decision.target_message_id,
            urgency=decision.urgency,
            persona_prompt=persona_prompt,
            user_prompt=user_prompt,
            soft_trigger=soft_trigger,
        )
        self._record(decision_id, topic.group_id, "PLAN", decision.contribution, now)

        try:
            text = await self.generation_model.generate(plan, topic, memories)
        except Exception:
            self._touch_favorability(topic, soft_trigger=soft_trigger, sent=False, now=now)
            return self._silent(decision_id, topic.group_id, "generation_error", now)

        speak = SpeakContract.resolve(text)
        if not speak.should_send:
            self._record(decision_id, topic.group_id, "SPEAK", speak.reason, now)
            self._touch_favorability(topic, soft_trigger=soft_trigger, sent=False, now=now)
            return self._silent(decision_id, topic.group_id, speak.reason, now)

        guarded = self.output_guard.validate(
            speak.text,
            recent_outputs=tuple(self._recent_outputs[topic.group_id]),
        )
        if not guarded.accepted and guarded.repairable:
            try:
                repaired = await self.generation_model.repair(speak.text, guarded.codes)
            except Exception:
                self._touch_favorability(
                    topic, soft_trigger=soft_trigger, sent=False, now=now
                )
                return self._silent(decision_id, topic.group_id, "repair_error", now)
            if SpeakContract.resolve(repaired).should_send is False:
                self._touch_favorability(
                    topic, soft_trigger=soft_trigger, sent=False, now=now
                )
                return self._silent(decision_id, topic.group_id, "model_silence", now)
            guarded = self.output_guard.validate(
                repaired,
                recent_outputs=tuple(self._recent_outputs[topic.group_id]),
            )
        if not guarded.accepted:
            self._touch_favorability(topic, soft_trigger=soft_trigger, sent=False, now=now)
            return self._silent(
                decision_id,
                topic.group_id,
                "guard_rejected:" + ",".join(guarded.codes),
                now,
            )
        self._record(decision_id, topic.group_id, "GUARD", "accepted", now)

        direct_wake = trigger in _HARD_TRIGGERS
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
            self._touch_favorability(topic, soft_trigger=soft_trigger, sent=False, now=now)
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
        self._remember_session_turns(topic, outbox_text, send_now)
        self._touch_favorability(
            topic, soft_trigger=soft_trigger, sent=True, now=send_now
        )
        self._record(decision_id, topic.group_id, "SEND", "sent", send_now)

        return WorkflowOutcome(
            decision_id=decision_id,
            sent=True,
            reason="sent",
            text=outbox_text,
        )

    def _build_decision(
        self,
        trigger: TriggerKind,
        topic: TopicSnapshot,
        soft_trigger: bool,
    ) -> Decision:
        target = topic.latest.message_id if topic.latest else None
        has_image = bool(self._topic_image_urls(topic))
        if soft_trigger:
            return Decision.respond(
                contribution="若话冲你且有一句自然短反应，就接一下；否则沉默",
                confidence=1.0,
                trigger=trigger,
                reason_code="soft_speak_contract",
                target_message_id=target,
                needs_vision=has_image,
                urgency=Urgency.NORMAL,
            )
        if trigger is TriggerKind.NATIVE_DIRECT:
            reason_code = "native_direct"
            contribution = "回应对方刚才的直接呼叫"
        elif trigger is TriggerKind.CONTINUATION:
            reason_code = "conversation_continuation"
            contribution = "继续回应对方刚才的对话"
        else:
            reason_code = "alias_direct"
            contribution = "回应对方刚才的直接呼叫"
        return Decision.respond(
            contribution=contribution,
            confidence=1.0,
            trigger=trigger,
            reason_code=reason_code,
            target_message_id=target,
            needs_vision=has_image,
            urgency=Urgency.HIGH,
        )

    def _ensure_favorability(self, topic: TopicSnapshot, now: int) -> Optional[int]:
        get = getattr(self.memory, "get_favorability", None)
        if get is None:
            return None
        active = select_active_messages(
            topic.messages, topic_created_at=topic.created_at
        )
        sender_id, sender_name = focus_speaker(active)
        if not sender_id:
            return None
        score = get(topic.group_id, sender_id)
        if score is not None:
            return int(score)
        relationships = {}
        assembly = getattr(self.persona, "assembly", None)
        if assembly is not None:
            relationships = getattr(assembly, "_relationships", {}) or {}
        _, relationship, _ = resolve_speaker(sender_id, sender_name, relationships)
        seed = seed_score_for_relationship(relationship)
        value = 0 if seed is None else int(seed)
        set_fav = getattr(self.memory, "set_favorability", None)
        if set_fav is not None:
            return int(set_fav(topic.group_id, sender_id, value, now))
        return value

    def _touch_favorability(
        self,
        topic: TopicSnapshot,
        *,
        soft_trigger: bool,
        sent: bool,
        now: int,
    ) -> None:
        adjust = getattr(self.memory, "adjust_favorability", None)
        if adjust is None:
            return
        active = select_active_messages(
            topic.messages, topic_created_at=topic.created_at
        )
        sender_id, _ = focus_speaker(active)
        if not sender_id:
            return
        latest_text = active[-1].text if active else ""
        delta = delta_for_turn(
            sent=sent, soft_trigger=soft_trigger, latest_text=latest_text
        )
        if delta == 0:
            return
        adjust(topic.group_id, sender_id, delta, now, default=0)

    def _remember_session_turns(
        self,
        topic: TopicSnapshot,
        assistant_text: str,
        timestamp: int,
    ) -> None:
        session = self.session_for(topic.group_id)
        latest = topic.latest
        if latest is not None and not latest.is_bot and latest.text:
            session.append_user(latest.sender_name or "群友", latest.text, latest.timestamp)
        session.append_assistant(
            assistant_text, timestamp, speaker=self.character_name
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

    async def _send_copied_at_tip(
        self,
        decision_id: str,
        topic: TopicSnapshot,
        trigger_alias: str,
        now: int,
    ) -> WorkflowOutcome:
        alias = (trigger_alias or "").strip() or "我"
        text = "AT{} 不能复制哦，复制的@为纯文本而非有效@".format(alias)
        self._record(decision_id, topic.group_id, "GATE", "copied_plain_at", now)
        try:
            await self.platform.send_text(topic.group_id, text, decision_id)
        except Exception as exc:
            return self._silent(
                decision_id,
                topic.group_id,
                "send_error:" + exc.__class__.__name__ + ":" + str(exc),
                now,
            )
        self._recent_outputs[topic.group_id].append(text)
        self._record(decision_id, topic.group_id, "SEND", "copied_at_tip", now)
        return WorkflowOutcome(
            decision_id=decision_id,
            sent=True,
            reason="copied_at_tip",
            text=text,
        )

    @staticmethod
    def _topic_image_urls(topic: TopicSnapshot) -> tuple:
        active = select_active_messages(
            topic.messages, topic_created_at=topic.created_at
        )
        urls = []
        for message in active:
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
