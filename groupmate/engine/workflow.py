"""Explicit cognitive workflow for deciding and producing group-chat replies."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
from dataclasses import replace
from typing import Callable, DefaultDict, Deque, Optional, Sequence, Tuple
from uuid import uuid4

from ..capabilities import (
    CapabilityGovernor,
    CapabilityRegistry,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
)
from ..capabilities.contracts import (
    CapabilityContext,
    CapabilityMediaPolicy,
    CapabilityPermission,
    validate_capability_name,
)
from ..core.addressee import AddresseeResolver
from ..core.intent import (
    has_image_capability,
    max_chars_for_mode,
    select_reply_mode,
)
from ..core.response_act import (
    ResponseAct,
    ResponseActPlan,
    TaskResolution,
    TaskResolutionStatus,
)
from ..core.session import GroupSession, GroupSessionStore
from ..core.scenes import classify_scene
from ..core.speak_contract import SpeakContract
from .composer import ResponseComposer
from .copied_at import copied_at_tip, is_copied_at
from .delivery import DeliveryService, build_delivery_plan
from .direct_fallback import DirectFallbackComposer
from .participation import ParticipationDecisionEngine
from .participation_types import (
    ParticipationAction,
    ParticipationObligation,
)
from ..models import (
    AddresseeKind,
    ChatMessage,
    ContinuityStatus,
    Decision,
    DecisionAction,
    InteractionScene,
    MessageOrigin,
    OutboundKind,
    ReplyMode,
    ReplyPlan,
    SelfCommitmentStatus,
    TargetingDecision,
    TopicSnapshot,
    TriggerKind,
    Urgency,
    WorkflowOutcome,
)
from ..persona.registry import PersonaContext
from ..policies import BehaviorPolicy
from ..ports import (
    Clock,
    GenerationModelPort,
    GuardResult,
    MemoryRepository,
    OutputGuard,
    PlatformPort,
    TraceSink,
    VisionPort,
)
from ..memory.memory_writer import MemoryWriter
from ..social.affinity import snapshot_for_relationship
from ..social.evidence import RelationshipEvidenceWriter
from ..social.continuity import ContinuityWriter, looks_like_open_loop
from ..social.commitments import SelfCommitmentWriter
from ..social.followups import ContinuityFollowupMatcher
from ..social.reminder_infer import (
    acceptance_fallback_for_request,
    infer_timed_reminder_from_topic,
    latest_user_text,
    looks_like_reminder_cancel,
    reminder_cancel_ack,
)
from .rate_limit import BudgetTracker, SlidingWindowRateLimiter
from .topics import select_active_messages

_SOFT_TRIGGERS = frozenset({TriggerKind.ALIAS_MENTION, TriggerKind.CANDIDATE})
_HARD_TRIGGERS = frozenset(
    {
        TriggerKind.ALIAS_DIRECT,
        TriggerKind.NATIVE_DIRECT,
        TriggerKind.CONTINUATION,
        TriggerKind.HOST_INTERACTION,
    }
)
TaskResponseResolver = Callable[
    [InteractionScene, ChatMessage],
    object,
]
FunContextProvider = Callable[[ChatMessage], str]


class CognitiveWorkflow:
    def __init__(
        self,
        generation_model: GenerationModelPort,
        vision: VisionPort,
        platform: PlatformPort,
        memory: MemoryRepository,
        persona_context: PersonaContext,
        behavior: BehaviorPolicy,
        vision_enabled: bool,
        output_guard: OutputGuard,
        rate_limiter: SlidingWindowRateLimiter,
        clock: Clock,
        trace: Optional[TraceSink] = None,
        sessions: Optional[GroupSessionStore] = None,
        character_name: str = "角色",
        delivery_service: Optional[DeliveryService] = None,
        addressee_resolver: Optional[AddresseeResolver] = None,
        participation_engine: Optional[ParticipationDecisionEngine] = None,
        direct_fallback: Optional[DirectFallbackComposer] = None,
        budgets: Optional[BudgetTracker] = None,
        memory_writer: Optional[MemoryWriter] = None,
        relationship_evidence_writer: Optional[RelationshipEvidenceWriter] = None,
        continuity_writer: Optional[ContinuityWriter] = None,
        self_commitment_writer: Optional[SelfCommitmentWriter] = None,
        continuity_followup_matcher: Optional[ContinuityFollowupMatcher] = None,
        task_response_resolver: Optional[TaskResponseResolver] = None,
        capabilities: Optional[CapabilityRegistry] = None,
        capability_governor: Optional[CapabilityGovernor] = None,
        composer: Optional[ResponseComposer] = None,
        poke_back_enabled: bool = False,
        relationship_learning_groups: Sequence[str] = (),
        relationship_learning_min_reviewed: int = 20,
        relationship_learning_max_error_rate: float = 0.10,
        fun_context_provider: Optional[FunContextProvider] = None,
    ) -> None:
        self.generation_model = generation_model
        self.vision = vision
        self.platform = platform
        self.memory = memory
        self.persona_context = persona_context
        self.persona = persona_context.prompt_provider
        self.behavior = behavior
        self.vision_enabled = bool(vision_enabled)
        self.poke_back_enabled = bool(poke_back_enabled)
        self.output_guard = output_guard
        self.rate_limiter = rate_limiter
        self.clock = clock
        self.trace = trace
        self.character_name = (character_name or "角色").strip() or "角色"
        self.delivery_service = delivery_service or DeliveryService(
            platform,
            memory,
            clock,
            persona_id=persona_context.persona_id,
            character_name=self.character_name,
        )
        self.sessions = sessions or GroupSessionStore(
            character_name=self.character_name
        )
        self.addressee_resolver = addressee_resolver or AddresseeResolver()
        self.budgets = budgets or BudgetTracker(rate_limiter)
        self.memory_writer = memory_writer or MemoryWriter(
            memory, persona_id=persona_context.persona_id
        )
        self.relationship_evidence_writer = (
            relationship_evidence_writer
            or RelationshipEvidenceWriter(
                memory,
                generation_model,
                persona_id=persona_context.persona_id,
                active_groups=relationship_learning_groups,
                min_reviewed_samples=relationship_learning_min_reviewed,
                max_error_rate=relationship_learning_max_error_rate,
            )
        )
        self.continuity_writer = continuity_writer or ContinuityWriter(
            memory,
            generation_model,
            persona_id=persona_context.persona_id,
        )
        self.self_commitment_writer = (
            self_commitment_writer
            or SelfCommitmentWriter(
                memory,
                generation_model,
                persona_id=persona_context.persona_id,
            )
        )
        self.continuity_followup_matcher = (
            continuity_followup_matcher
            or ContinuityFollowupMatcher(
                memory,
                generation_model,
                persona_id=persona_context.persona_id,
            )
        )
        self.participation_engine = (
            participation_engine or ParticipationDecisionEngine()
        )
        self.direct_fallback = direct_fallback or DirectFallbackComposer()
        self.task_response_resolver = task_response_resolver
        self.capabilities = capabilities
        self.capability_governor = (
            capability_governor
            if capability_governor is not None
            else (
                CapabilityGovernor(capabilities)
                if capabilities is not None
                else None
            )
        )
        self.composer = composer or ResponseComposer()
        self.fun_context_provider = fun_context_provider
        self._recent_outputs: DefaultDict[str, Deque[str]] = defaultdict(
            lambda: deque(maxlen=20)
        )

    def hydrate_recent_outputs(self, group_id: str, texts: Sequence[str]) -> None:
        bucket = self._recent_outputs[str(group_id)]
        bucket.clear()
        for text in texts:
            cleaned = (text or "").strip()
            if cleaned:
                bucket.append(cleaned)

    def session_for(self, group_id: str) -> GroupSession:
        return self.sessions.get(group_id)

    async def evaluate(
        self,
        topic: TopicSnapshot,
        trigger: TriggerKind,
        behavior: BehaviorPolicy,
        trigger_alias: str = "",
        still_valid: Optional[Callable[[], bool]] = None,
    ) -> WorkflowOutcome:
        decision_id = uuid4().hex
        now = self.clock.now()
        soft_trigger = trigger in _SOFT_TRIGGERS
        self._record(decision_id, topic.group_id, "OBSERVE", trigger.value, now)

        if not topic.messages:
            return self._silent(decision_id, topic.group_id, "empty_topic", now)
        if trigger in (TriggerKind.IGNORE, TriggerKind.COMMAND):
            return self._silent(decision_id, topic.group_id, "bypassed_trigger", now)
        if is_copied_at(trigger):
            return await self._send_copied_at_tip(
                decision_id, topic, trigger_alias, now, still_valid
            )

        topic = self._canonical_member_topic(topic)

        scene = classify_scene(trigger, topic.latest)
        self._record(decision_id, topic.group_id, "SCENE", scene.value, now)

        targeting = self._resolve_targeting(topic, trigger)
        self._record(
            decision_id,
            topic.group_id,
            "ADDRESSEE",
            self._targeting_trace(topic, targeting),
            now,
        )
        cancelled = self.self_commitment_writer.cancel_open_reminder_for_sender(
            topic,
            decision_id=decision_id,
            now=now,
        )
        latest_text = latest_user_text(topic)
        reminder_cancelled = cancelled is not None
        # Continuation after「小爱，N分钟后提醒我」is a hard wake. The latest
        # turn may still be a cancel even if the ledger row was missed.
        cancel_turn = reminder_cancelled or looks_like_reminder_cancel(
            latest_text,
            has_open_reminder=reminder_cancelled,
        )
        if reminder_cancelled:
            self._record(
                decision_id,
                topic.group_id,
                "COMMITMENT",
                "reminder_cancelled",
                now,
            )

        followup_match = None
        try:
            followup_match = await self.continuity_followup_matcher.match(
                topic,
                trigger=trigger,
                decision_id=decision_id,
                now=now,
            )
        except Exception:  # noqa: BLE001 - matching must never break a turn
            self._record(
                decision_id,
                topic.group_id,
                "FOLLOWUP",
                "match_failed",
                now,
            )
        else:
            if followup_match is not None:
                self._record(
                    decision_id,
                    topic.group_id,
                    "FOLLOWUP",
                    "{}:{}".format(
                        followup_match.event.outcome.value,
                        followup_match.event.response_policy,
                    ),
                    now,
                )

        relationship_state = self._relationship_state_for_target(topic, targeting)
        affinity = snapshot_for_relationship(
            relationship_state,
            configured_relationship=self._configured_relationship_for(
                topic.group_id,
                self._social_user_id(targeting)
            ),
        )
        task_resolution, task_resolution_reason = self._task_response_inputs(
            scene,
            topic.latest,
        )
        if task_resolution_reason:
            self._record(
                decision_id,
                topic.group_id,
                "TASK_RESOLUTION",
                task_resolution_reason,
                now,
            )
        persona_profile = self.persona_context.definition.participation_profile
        participation = self.participation_engine.decide(
            persona_id=self.persona_context.persona_id,
            topic=topic,
            trigger=trigger,
            policy=behavior.participation,
            targeting=targeting,
            now=now,
            aliases=self.persona_context.aliases,
            affinity=affinity,
            persona=persona_profile,
            recent_outputs=tuple(self._recent_outputs[topic.group_id]),
            task_resolution=task_resolution,
            interaction=behavior.interaction,
        )
        if cancel_turn:
            participation = replace(
                participation,
                action=ParticipationAction.SPEAK,
                act=participation.act or ResponseAct.ACKNOWLEDGE,
                obligation=ParticipationObligation.DIRECT_REQUIRED,
                contribution=(
                    "对方取消了提醒，用自己的口吻短答应；不要再喊提醒内容，"
                    "不要重新答应倒计时"
                ),
            )
        elif followup_match is not None and (
            participation.obligation is ParticipationObligation.DIRECT_REQUIRED
            or (
                followup_match.should_speak
                and participation.action is ParticipationAction.SILENCE
                and "inhibit:owned_by_other_user" not in participation.reason_codes
                and "inhibit:passing_alias_mention" not in participation.reason_codes
            )
        ):
            participation = replace(
                participation,
                action=ParticipationAction.SPEAK,
                act=participation.act or ResponseAct.ACKNOWLEDGE,
                obligation=(
                    participation.obligation
                    if participation.action is ParticipationAction.SPEAK
                    else ParticipationObligation.OPEN_OPTIONAL
                ),
                reason_codes=participation.reason_codes + (
                    "motive:continuity_followup",
                ),
                contribution=followup_match.contribution,
            )
        self._record(
            decision_id,
            topic.group_id,
            "PARTICIPATION",
            ",".join(participation.reason_codes),
            now,
        )
        if participation.action is ParticipationAction.SILENCE:
            reason = (
                participation.reason_codes[-1]
                if participation.reason_codes
                else "participation_silence"
            )
            if followup_match is None:
                await self._maybe_observe_continuity(
                    topic,
                    targeting,
                    decision_id=decision_id,
                    now=now,
                    reason_codes=participation.reason_codes,
                )
            return self._silent(decision_id, topic.group_id, reason, now)

        if (
            participation.obligation is ParticipationObligation.OPEN_OPTIONAL
            and not self.budgets.allow_send(now)
        ):
            return self._silent(
                decision_id,
                topic.group_id,
                "open_send_budget_exhausted",
                now,
            )

        scene = participation.scene
        if participation.act is ResponseAct.BOUNDARY:
            reply_mode = ReplyMode.BOUNDARY
        else:
            reply_mode = select_reply_mode(
                topic.latest.text if topic.latest is not None else "",
                soft_trigger=soft_trigger,
            )
        response_act = ResponseActPlan(
            act=participation.act,
            scene=scene,
            reason_codes=participation.reason_codes,
            required_information=task_resolution.required_information,
            capability_name=task_resolution.capability_name,
        )
        image_urls = self._topic_image_urls(topic)
        needs_vision = bool(
            self.vision_enabled
            and participation.act
            in (ResponseAct.ANSWER, ResponseAct.VISUAL_REACTION)
            and has_image_capability(image_urls, reply_mode)
        )
        required_capabilities = ("vision",) if needs_vision else ()
        contribution = participation.contribution
        target_message_id = (
            targeting.reply_audience.target_message_id
            or (topic.latest.message_id if topic.latest is not None else None)
        )
        urgency = (
            Urgency.HIGH
            if participation.obligation
            is ParticipationObligation.DIRECT_REQUIRED
            else Urgency.NORMAL
        )
        decision_reason = "participation_speak"
        if trigger is TriggerKind.HOST_INTERACTION:
            interaction_decision = self._build_decision(
                trigger,
                topic,
                soft_trigger,
            )
            if "poke_direct" in participation.reason_codes:
                # Keep participation's pressure-aware contribution.
                contribution = participation.contribution or interaction_decision.contribution
            elif participation.act is ResponseAct.PLAYFUL_REPLY:
                contribution = interaction_decision.contribution
            urgency = interaction_decision.urgency
            decision_reason = interaction_decision.reason_code
            if "poke_bystander" in participation.reason_codes:
                decision_reason = "poke_bystander"
                urgency = Urgency.NORMAL
        quote_mode = participation.quote_mode
        self._record(
            decision_id,
            topic.group_id,
            "INTENT",
            "{}:{}".format(reply_mode.value, participation.act.value),
            now,
        )
        decision = Decision.respond(
            contribution=contribution,
            confidence=1.0,
            trigger=trigger,
            reason_code=decision_reason,
            target_message_id=target_message_id,
            needs_vision=needs_vision,
            urgency=urgency,
        )

        self._record(
            decision_id,
            topic.group_id,
            "ACT",
            response_act.act.value,
            now,
        )

        self._record(
            decision_id,
            topic.group_id,
            "GATE",
            decision.reason_code or decision.action.value,
            now,
        )
        if decision.action is not DecisionAction.RESPOND:
            return self._silent(decision_id, topic.group_id, "decision_ignore", now)

        if (
            self.poke_back_enabled
            and "poke_bystander" in participation.reason_codes
        ):
            poke_only = await self._deliver_poke_only(
                decision_id=decision_id,
                topic=topic,
                participation=participation,
                response_act=response_act,
                urgency=urgency,
                behavior=behavior,
                soft_trigger=soft_trigger,
                still_valid=still_valid,
                now=now,
                affinity_band=affinity.band,
            )
            if poke_only is not None:
                return poke_only

        active = select_active_messages(
            topic.messages, topic_created_at=topic.created_at
        )
        query = " ".join(message.text for message in active if message.text)
        memory_subject = targeting.memory_subject
        no_personal = (
            memory_subject.kind is AddresseeKind.AMBIGUOUS
            or "no_personal_memory" in memory_subject.reason_codes
        )
        if (
            not no_personal
            and memory_subject.kind is AddresseeKind.USER
            and memory_subject.target_user_ids
        ):
            subject_ids = tuple(memory_subject.target_user_ids)
            include_user_in_group = True
        elif no_personal:
            subject_ids = ()
            include_user_in_group = False
        else:
            subject_ids = tuple(
                dict.fromkeys(
                    message.sender_id
                    for message in active
                    if message.sender_id and not message.is_bot
                )
            )
            include_user_in_group = True
        memories = list(
            self.memory.search_memories(
                self.persona_context.persona_id,
                topic.group_id,
                query,
                now,
                8,
                subject_ids=subject_ids,
                include_user_in_group=include_user_in_group,
            )
        )
        continuity_items = []
        self_commitments = []
        if subject_ids:
            continuity_items = list(
                self.memory.list_continuity_items(
                    self.persona_context.persona_id,
                    group_id=topic.group_id,
                    subject_ids=subject_ids,
                    statuses=(ContinuityStatus.OPEN,),
                    limit=5,
                )
            )
            self_commitments = list(
                self.memory.list_self_commitments(
                    self.persona_context.persona_id,
                    group_id=topic.group_id,
                    beneficiary_subject_ids=subject_ids,
                    statuses=(
                        SelfCommitmentStatus.PENDING,
                        SelfCommitmentStatus.IN_PROGRESS,
                        SelfCommitmentStatus.BLOCKED,
                    ),
                    limit=5,
                )
            )
        self._record(
            decision_id,
            topic.group_id,
            "RECALL",
            "memories={};continuity={};commitments={}".format(
                len(memories), len(continuity_items), len(self_commitments)
            ),
            now,
        )

        capability_result = None
        capability_name = self._capability_name(
            response_act, required_capabilities
        )
        if capability_name and self.capability_governor is not None:
            capability_result = await self._execute_capability(
                decision_id,
                topic,
                capability_name,
                participation.media_policy,
                now,
            )
            if capability_result.status is CapabilityStatus.SUCCESS:
                contribution = self._successful_capability_contribution(response_act)
            else:
                contribution = self._incomplete_capability_contribution(
                    response_act, capability_result
                )

        capability_facts = (
            capability_result.facts
            if capability_result is not None
            and capability_result.status is CapabilityStatus.SUCCESS
            else ()
        )
        capability_status = (
            capability_result.status.value
            if capability_result is not None
            else (
                CapabilityStatus.UNSUPPORTED.value
                if response_act.act is ResponseAct.TASK_UNSUPPORTED
                else ""
            )
        )
        session = self.session_for(topic.group_id)
        timed_request = (
            None
            if cancel_turn
            else infer_timed_reminder_from_topic(topic, now=int(now))
        )
        source_user_text = (
            timed_request.source_text
            if timed_request is not None
            else (latest_text or (topic.latest.text if topic.latest is not None else ""))
        )
        effective_contribution = contribution or decision.contribution
        if cancel_turn:
            effective_contribution = (
                "对方取消了尚未到期的提醒：短答应即可，不要再喊提醒内容，"
                "不要重新答应倒计时"
            )
        elif timed_request is not None:
            effective_contribution = (
                "对方在约未来到点提醒：现在只答应并确认倒计时开始，"
                "不要现在就喊提醒内容；到点再说"
            )
        assemble_kwargs = {
            "contribution": effective_contribution,
            "soft_trigger": soft_trigger,
            "session": session,
            "relationship_state": relationship_state,
            "targeting": targeting,
            "reply_mode": reply_mode,
            "continuity_items": continuity_items,
            "self_commitments": self_commitments,
            "fun_context": (
                self.fun_context_provider(topic.latest)
                if self.fun_context_provider is not None and topic.latest is not None
                else ""
            ),
        }
        assemble_kwargs.update(
            response_act=response_act,
            capability_facts=capability_facts,
            capability_status=capability_status,
        )
        assembled = self.persona.assemble(topic, memories, **assemble_kwargs)
        persona_prompt = assembled.system
        user_prompt = assembled.user

        plan = ReplyPlan(
            decision_id=decision_id,
            group_id=topic.group_id,
            trigger=trigger,
            contribution=effective_contribution,
            target_message_id=target_message_id,
            urgency=urgency,
            persona_prompt=persona_prompt,
            user_prompt=user_prompt,
            soft_trigger=soft_trigger,
            reply_mode=reply_mode,
            response_act=response_act,
            required_capabilities=required_capabilities,
        )
        self._record(decision_id, topic.group_id, "PLAN", plan.contribution, now)

        direct_required = (
            participation.obligation
            is ParticipationObligation.DIRECT_REQUIRED
        )
        fallback_used = False
        timed_reminder_request = timed_request is not None

        def fallback_text(reason: str) -> str:
            self._record(decision_id, topic.group_id, "FALLBACK", reason, now)
            return self.direct_fallback.compose(
                participation.act,
                participation.posture,
                reason_codes=participation.reason_codes,
            )

        def reminder_ack_text(reason: str) -> str:
            self._record(decision_id, topic.group_id, "FALLBACK", reason, now)
            if cancel_turn:
                return reminder_cancel_ack()
            if timed_request is not None:
                return timed_request.acceptance_text()
            return acceptance_fallback_for_request(source_user_text)

        if cancel_turn and direct_required:
            text = reminder_ack_text("reminder_cancel_fallback")
            fallback_used = True
        elif not self.budgets.allow_generation(now):
            if (timed_reminder_request or cancel_turn) and direct_required:
                text = reminder_ack_text(
                    "reminder_cancel_fallback"
                    if cancel_turn
                    else "timed_reminder_accept"
                )
                fallback_used = True
            elif not direct_required:
                return self._silent(
                    decision_id,
                    topic.group_id,
                    "generation_budget_exhausted",
                    now,
                )
            else:
                text = fallback_text("generation_budget_exhausted")
                fallback_used = True
        else:
            try:
                text = await self.generation_model.generate(plan, topic, memories)
            except Exception:
                if (timed_reminder_request or cancel_turn) and direct_required:
                    text = reminder_ack_text(
                        "reminder_cancel_fallback"
                        if cancel_turn
                        else "timed_reminder_accept"
                    )
                    fallback_used = True
                elif not direct_required:
                    return self._silent(
                        decision_id, topic.group_id, "generation_error", now
                    )
                else:
                    text = fallback_text("generation_error")
                    fallback_used = True
            else:
                self.budgets.record_generation(now)

        speak = SpeakContract.resolve(text)
        if not speak.should_send:
            self._record(decision_id, topic.group_id, "SPEAK", speak.reason, now)
            if not direct_required:
                return self._silent(decision_id, topic.group_id, speak.reason, now)
            if timed_reminder_request or cancel_turn:
                text = reminder_ack_text(
                    "reminder_cancel_fallback"
                    if cancel_turn
                    else "timed_reminder_accept"
                )
            else:
                text = fallback_text(speak.reason)
            fallback_used = True
            speak = SpeakContract.resolve(text)

        guarded = self._validate_output(
            speak.text,
            topic.group_id,
            reply_mode,
            response_act,
            capability_status,
            source_text=source_user_text,
            ignore_recent=fallback_used,
        )
        if (
            not guarded.accepted
            and timed_reminder_request
            and direct_required
            and "premature_reminder_delivery" in guarded.codes
        ):
            fallback_used = True
            guarded = self._validate_output(
                reminder_ack_text("timed_reminder_accept"),
                topic.group_id,
                reply_mode,
                response_act,
                capability_status,
                source_text=source_user_text,
                ignore_recent=True,
            )
        elif not guarded.accepted and guarded.repairable and not fallback_used:
            try:
                repaired = await self.generation_model.repair(speak.text, guarded.codes)
            except Exception:
                if not direct_required:
                    return self._silent(
                        decision_id, topic.group_id, "repair_error", now
                    )
                fallback_used = True
                repaired = (
                    reminder_ack_text(
                        "reminder_cancel_fallback"
                        if cancel_turn
                        else "timed_reminder_accept"
                    )
                    if (timed_reminder_request or cancel_turn)
                    else fallback_text("repair_error")
                )
            if SpeakContract.resolve(repaired).should_send is False:
                if not direct_required:
                    return self._silent(
                        decision_id, topic.group_id, "model_silence", now
                    )
                fallback_used = True
                repaired = (
                    reminder_ack_text(
                        "reminder_cancel_fallback"
                        if cancel_turn
                        else "timed_reminder_accept"
                    )
                    if (timed_reminder_request or cancel_turn)
                    else fallback_text("repair_silence")
                )
            guarded = self._validate_output(
                repaired,
                topic.group_id,
                reply_mode,
                response_act,
                capability_status,
                source_text=source_user_text,
                ignore_recent=fallback_used,
            )
        if not guarded.accepted and direct_required and not fallback_used:
            fallback_used = True
            if cancel_turn:
                fallback = reminder_ack_text("reminder_cancel_fallback")
            elif timed_reminder_request or (
                "premature_reminder_delivery" in guarded.codes
            ):
                fallback = reminder_ack_text("timed_reminder_accept")
            else:
                fallback = fallback_text("guard_rejected")
            guarded = self._validate_output(
                fallback,
                topic.group_id,
                reply_mode,
                response_act,
                capability_status,
                source_text=source_user_text,
                ignore_recent=True,
            )
        # 定时提醒兜底若仍被拦（极少见），强制接受确定性答应文案
        if (
            not guarded.accepted
            and direct_required
            and timed_reminder_request
        ):
            forced = reminder_ack_text("timed_reminder_force")
            guarded = self.output_guard.validate(
                forced,
                recent_outputs=(),
                reply_mode=reply_mode,
                response_act=(
                    response_act.act if response_act is not None else None
                ),
                capability_status=capability_status,
                source_text=source_user_text,
            )
            if not guarded.accepted:
                guarded = GuardResult(
                    accepted=True,
                    text=forced,
                    codes=(),
                    repairable=False,
                )
        if not guarded.accepted:
            return self._silent(
                decision_id,
                topic.group_id,
                "guard_rejected:" + ",".join(guarded.codes),
                now,
            )
        self._record(decision_id, topic.group_id, "GUARD", "accepted", now)

        direct_wake = trigger in _HARD_TRIGGERS
        mode_max = max_chars_for_mode(reply_mode)
        quote_message_id = None
        if quote_mode.value == "always" or (
            quote_mode.value == "when_interleaved"
            and self._has_interleaved_context(topic, target_message_id)
        ):
            quote_message_id = target_message_id
        delivery = build_delivery_plan(
            decision_id=decision_id,
            group_id=topic.group_id,
            text=guarded.text,
            urgency=urgency,
            now=self.clock.now(),
            ttl_seconds=behavior.conversation.candidate_ttl_seconds,
            max_chars=mode_max,
            max_segments=behavior.reply.max_reply_segments,
            humanize_delay=behavior.reply.humanize_delay_enabled,
            direct_wake=direct_wake,
            quote_message_id=quote_message_id,
        )
        draft = self.composer.compose(
            text=guarded.text,
            act_plan=response_act,
            quote_message_id=quote_message_id,
            capability_result=capability_result,
            poke_back_enabled=self.poke_back_enabled,
            poke_role=self._poke_role(topic),
            poke_target_user_id=self._poke_outbound_target(
                topic, participation, behavior.interaction
            ),
            interaction=behavior.interaction,
            affinity_band=affinity.band,
            pressure=participation.pressure,
            reason_codes=participation.reason_codes,
        )
        delivery = replace(delivery, outbound=draft.segments)
        media_ids = tuple(item.media_id for item in draft.segments if item.media_id)
        self._record(
            decision_id,
            topic.group_id,
            "COMPOSE",
            "act={};media={}".format(
                response_act.act.value,
                ",".join(media_ids) if media_ids else "0",
            ),
            now,
        )
        if not delivery.segments and not delivery.outbound:
            return self._silent(decision_id, topic.group_id, "empty_delivery", now)
        self._record(
            decision_id,
            topic.group_id,
            "SCHEDULE",
            "delay={:.2f};segments={}".format(
                delivery.delay_seconds,
                len(delivery.outbound) if delivery.outbound else len(delivery.segments),
            ),
            now,
        )

        outcome = await self.delivery_service.deliver(
            delivery,
            kind="candidate" if soft_trigger else "reply",
            still_valid=still_valid,
            sent_reason="sent",
        )
        send_now = self.clock.now()
        if not outcome.sent:
            if followup_match is not None:
                self.memory.reopen_continuity_item_after_unsent_followup(
                    self.persona_context.persona_id,
                    followup_match.event.event_id,
                    now=send_now,
                )
            self._record(
                decision_id, topic.group_id, "END", outcome.reason, send_now
            )
            return outcome
        self._commit_poke_throttle(
            topic=topic,
            participation=participation,
            now=send_now,
        )
        if participation.obligation is ParticipationObligation.OPEN_OPTIONAL:
            self.budgets.record_send(send_now)
        self._recent_outputs[topic.group_id].append(outcome.text)
        if followup_match is not None:
            self.memory.mark_continuity_followup_sent(
                self.persona_context.persona_id,
                followup_match.event.event_id,
                sent_at=send_now,
            )
        self._remember_session_turns(topic, outcome.text, send_now)
        try:
            self.memory_writer.schedule_after_send(
                topic,
                targeting,
                decision_id=decision_id,
                now=send_now,
                reply_text=outcome.text or "",
            )
        except Exception:  # noqa: BLE001 — MemoryWriter 不得影响主回复
            self._record(
                decision_id, topic.group_id, "MEMORY", "schedule_failed", send_now
            )
        try:
            self.continuity_writer.schedule_after_send(
                topic,
                targeting,
                decision_id=decision_id,
                now=send_now,
                reply_text=outcome.text or "",
            )
        except Exception:  # noqa: BLE001 - continuity never breaks replies
            self._record(
                decision_id,
                topic.group_id,
                "CONTINUITY",
                "schedule_failed",
                send_now,
            )
        try:
            self.self_commitment_writer.schedule_after_send(
                topic,
                targeting,
                decision_id=decision_id,
                now=send_now,
                reply_text=outcome.text or "",
                capability_result=capability_result,
            )
        except Exception:  # noqa: BLE001 - commitments never break replies
            self._record(
                decision_id,
                topic.group_id,
                "COMMITMENT",
                "schedule_failed",
                send_now,
            )
        try:
            evidence = await self.relationship_evidence_writer.process(
                topic,
                targeting,
                trigger=trigger,
                decision_id=decision_id,
                now=send_now,
                response_act=response_act,
                reply_text=outcome.text or "",
                participation_reasons=participation.reason_codes,
                configured_relationship=self._configured_relationship_for(
                    topic.group_id,
                    self._social_user_id(targeting)
                ),
            )
        except Exception:  # noqa: BLE001 — relationship evidence never breaks replies
            self._record(
                decision_id,
                topic.group_id,
                "RELATIONSHIP",
                "evidence_failed",
                send_now,
            )
        else:
            if evidence is not None:
                self._record(
                    decision_id,
                    topic.group_id,
                    "RELATIONSHIP",
                    "accepted:" + evidence.kind.value,
                    send_now,
                )
        return outcome

    def _task_response_inputs(
        self,
        scene: InteractionScene,
        message: Optional[ChatMessage],
    ) -> Tuple[TaskResolution, str]:
        if scene is not InteractionScene.TASK_REQUEST or message is None:
            return TaskResolution(), ""
        if self.task_response_resolver is None:
            return TaskResolution(), "resolver_missing"
        try:
            value = self.task_response_resolver(scene, message)
        except Exception as exc:  # noqa: BLE001 - capability boundary fails closed
            return (
                TaskResolution(),
                "resolver_error:{}".format(type(exc).__name__),
            )
        if value is None:
            return TaskResolution(), "resolver_none"
        if not isinstance(value, TaskResolution):
            return TaskResolution(), "resolver_invalid"
        resolution = value
        if resolution.required_information:
            reason = "needs_information"
        elif resolution.status is TaskResolutionStatus.SUPPORTED:
            reason = "supported"
        elif resolution.status is TaskResolutionStatus.UNSUPPORTED:
            reason = "unsupported"
        else:
            reason = "unknown"
        if resolution.capability_name:
            reason += ":" + resolution.capability_name
        return resolution, reason

    @staticmethod
    def _capability_name(
        response_act: Optional[ResponseActPlan],
        required_capabilities: Sequence[str],
    ) -> str:
        if response_act is not None and response_act.capability_name:
            try:
                return validate_capability_name(response_act.capability_name)
            except (TypeError, ValueError):
                return ""
        for name in required_capabilities or ():
            cleaned = str(name or "").strip()
            if cleaned:
                try:
                    return validate_capability_name(cleaned)
                except (TypeError, ValueError):
                    return ""
        return ""

    async def _execute_capability(
        self,
        decision_id: str,
        topic: TopicSnapshot,
        capability_name: str,
        media_policy,
        now: int,
    ) -> CapabilityResult:
        if capability_name == "vision" and not self.vision_enabled:
            result = CapabilityResult(
                CapabilityStatus.UNSUPPORTED,
                capability_name,
                user_text="Vision is disabled for this group.",
                error_code="vision_disabled",
            )
        elif capability_name == "vision" and not self.budgets.allow_cost(now):
            result = CapabilityResult(
                CapabilityStatus.FAILED,
                capability_name,
                user_text="Vision could not run within the current budget.",
                error_code="cost_budget_exhausted",
            )
        elif self.capability_governor is None:
            result = CapabilityResult(
                CapabilityStatus.UNSUPPORTED,
                capability_name,
                user_text="This capability is not available.",
                error_code="capability_not_registered",
            )
        else:
            latest = topic.latest
            request = CapabilityRequest(
                capability_name=capability_name,
                message_text=latest.text if latest is not None else "",
                media_locators=self._topic_image_urls(topic),
                group_id=topic.group_id,
                actor_id=latest.sender_id if latest is not None else "",
                message_id=latest.message_id if latest is not None else "",
            )
            context = CapabilityContext(
                persona_id=self.persona_context.persona_id,
                group_id=topic.group_id,
                actor_id=latest.sender_id if latest is not None else "",
                message_id=latest.message_id if latest is not None else "",
                trace_id=decision_id,
                deadline_at=now + 10,
                allowed_permissions=(
                    (CapabilityPermission.VISION_READ,)
                    if capability_name == "vision"
                    else ()
                ),
                media_policy=CapabilityMediaPolicy(
                    capability_media_allowed=bool(
                        getattr(
                            media_policy,
                            "capability_media_allowed",
                            False,
                        )
                    ),
                    allowed_media_kinds=("image",),
                    allowed_safety_labels=(
                        "catalog_approved",
                        "provider_approved",
                        "reviewed",
                        "safe",
                    ),
                ),
            )
            try:
                result = await self.capability_governor.execute(
                    request,
                    context,
                    now=now,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - capability boundary fails closed
                result = CapabilityResult(
                    CapabilityStatus.FAILED,
                    capability_name,
                    user_text="The capability could not complete the request.",
                    error_code="execution_error",
                    diagnostic=type(exc).__name__,
                )
        if (
            capability_name == "vision"
            and result.status is CapabilityStatus.SUCCESS
        ):
            self.budgets.record_cost(now)
        self._record(
            decision_id,
            topic.group_id,
            "CAPABILITY",
            result.status.value,
            now,
        )
        return result

    @staticmethod
    def _successful_capability_contribution(
        response_act: Optional[ResponseActPlan],
    ) -> str:
        if response_act is not None and response_act.act is ResponseAct.VISUAL_REACTION:
            return "根据本轮已验证的图片事实，用爱弥斯的口吻自然短评"
        return "根据本轮已验证事实直接回应，用爱弥斯自己的口吻表达"

    @staticmethod
    def _incomplete_capability_contribution(
        response_act: Optional[ResponseActPlan],
        result: CapabilityResult,
    ) -> str:
        if result.status is CapabilityStatus.UNSUPPORTED:
            return "简短说明当前无法完成这项任务，不得编造结果"
        if result.status is CapabilityStatus.HANDOFF:
            return "简短说明任务仍在交接中，不得声称已经完成"
        if result.status is CapabilityStatus.TIMEOUT:
            return "简短说明这次没有得到结果，可以稍后重试"
        if response_act is not None and response_act.act is ResponseAct.TASK_UNSUPPORTED:
            return "简短说明当前无法完成这项任务"
        return "简短说明这次没有成功完成，不得编造结果"

    def _validate_output(
        self,
        text: str,
        group_id: str,
        reply_mode: ReplyMode,
        response_act: Optional[ResponseActPlan],
        capability_status: str,
        *,
        source_text: str = "",
        ignore_recent: bool = False,
    ):
        recent = () if ignore_recent else tuple(self._recent_outputs[group_id])
        return self.output_guard.validate(
            text,
            recent_outputs=recent,
            reply_mode=reply_mode,
            response_act=response_act.act if response_act is not None else None,
            capability_status=capability_status,
            source_text=source_text,
        )

    def _resolve_targeting(
        self,
        topic: TopicSnapshot,
        trigger: TriggerKind,
    ) -> TargetingDecision:
        relationships = self.persona.assembly.relationships
        name_index = self.memory.member_name_index(
            self.persona_context.persona_id, topic.group_id
        )
        return self.addressee_resolver.resolve(
            topic,
            trigger,
            aliases=self.persona_context.aliases,
            relationships=relationships,
            name_index=name_index,
        )

    def _canonical_member_topic(self, topic: TopicSnapshot) -> TopicSnapshot:
        persona_id = self.persona_context.persona_id
        group_id = topic.group_id
        changed = False
        messages = []
        for message in topic.messages:
            if message.is_bot:
                messages.append(message)
                continue
            sender_id = self.memory.resolve_member_subject_id(
                persona_id, group_id, message.sender_id
            )
            sender_name = self.memory.member_display_name(
                persona_id, group_id, sender_id
            ) or message.sender_name
            mention_names = dict(message.metadata.get("mention_names") or {})
            anonymous_ids = []
            raw_anonymous = {
                str(item)
                for item in (message.metadata.get("anonymous_mention_ids") or ())
            }
            mentioned = []
            text = str(message.text or "")
            for raw_id in message.mentioned_user_ids:
                canonical_id = self.memory.resolve_member_subject_id(
                    persona_id, group_id, raw_id
                )
                if canonical_id not in mentioned:
                    mentioned.append(canonical_id)
                canonical_name = self.memory.member_display_name(
                    persona_id, group_id, canonical_id
                )
                old_name = str(mention_names.get(str(raw_id), "") or "").strip()
                if canonical_name:
                    mention_names[canonical_id] = canonical_name
                    mention_names[str(raw_id)] = canonical_name
                    if old_name and old_name != canonical_name:
                        text = text.replace("@" + old_name, "@" + canonical_name)
                if str(raw_id) in raw_anonymous and canonical_id not in anonymous_ids:
                    anonymous_ids.append(canonical_id)
            metadata = dict(
                message.metadata,
                mention_names=mention_names,
                anonymous_mention_ids=anonymous_ids,
                canonical_sender_id=sender_id,
                canonical_sender_name=sender_name,
            )
            canonical = replace(
                message,
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
                mentioned_user_ids=tuple(mentioned),
                metadata=metadata,
            )
            changed = changed or canonical != message
            messages.append(canonical)
        if not changed:
            return topic
        return replace(topic, messages=tuple(messages))

    def _targeting_trace(
        self, topic: TopicSnapshot, targeting: TargetingDecision
    ) -> str:
        names = {
            message.sender_id: message.sender_name
            for message in topic.messages
            if message.sender_id and message.sender_name
        }
        for message in topic.messages:
            mention_names = message.metadata.get("mention_names") or {}
            if isinstance(mention_names, dict):
                names.update(
                    {
                        str(user_id): str(name)
                        for user_id, name in mention_names.items()
                        if str(user_id) and str(name).strip()
                    }
                )

        def payload(resolution):
            user_id = (
                resolution.target_user_ids[0]
                if resolution.target_user_ids
                else ""
            )
            return {
                "kind": resolution.kind.value,
                "name": names.get(user_id, ""),
                "source": (
                    resolution.reason_codes[0]
                    if resolution.reason_codes
                    else resolution.kind.value
                ),
                "confidence": round(float(resolution.confidence), 2),
            }

        return json.dumps(
            {
                "reply": payload(targeting.reply_audience),
                "social": payload(targeting.social_target),
                "memory": payload(targeting.memory_subject),
            },
            ensure_ascii=False,
            sort_keys=True,
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
        if trigger is TriggerKind.HOST_INTERACTION:
            reason_code = "host_interaction"
            contribution = "回应对方刚才对你的戳一戳互动，短而自然"
        elif trigger is TriggerKind.NATIVE_DIRECT:
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

    @staticmethod
    def _poke_role(topic: TopicSnapshot) -> str:
        latest = topic.latest
        if latest is None:
            return ""
        return str(latest.metadata.get("poke_role", "") or "").strip().lower()

    async def _deliver_poke_only(
        self,
        *,
        decision_id: str,
        topic: TopicSnapshot,
        participation,
        response_act,
        urgency: Urgency,
        behavior,
        soft_trigger: bool,
        still_valid: Optional[Callable[[], bool]],
        now: int,
        affinity_band=None,
    ):
        target = self._poke_outbound_target(
            topic, participation, behavior.interaction
        )
        if not target:
            return None
        draft = self.composer.compose(
            text="",
            act_plan=response_act,
            quote_message_id=None,
            poke_back_enabled=True,
            poke_role=self._poke_role(topic) or "bystander",
            poke_target_user_id=target,
            interaction=behavior.interaction,
            affinity_band=affinity_band,
            pressure=participation.pressure,
            reason_codes=participation.reason_codes,
        )
        if not any(item.kind is OutboundKind.POKE for item in draft.segments):
            return None
        delivery = build_delivery_plan(
            decision_id=decision_id,
            group_id=topic.group_id,
            text="",
            urgency=urgency,
            now=self.clock.now(),
            ttl_seconds=behavior.conversation.candidate_ttl_seconds,
            max_chars=1,
            max_segments=1,
            humanize_delay=behavior.reply.humanize_delay_enabled,
            direct_wake=False,
            quote_message_id=None,
        )
        delivery = replace(delivery, outbound=draft.segments, segments=())
        self._record(
            decision_id,
            topic.group_id,
            "COMPOSE",
            "act={};media=0;poke_only".format(response_act.act.value),
            now,
        )
        self._record(
            decision_id,
            topic.group_id,
            "SCHEDULE",
            "delay={:.2f};segments={}".format(
                delivery.delay_seconds,
                len(delivery.outbound),
            ),
            now,
        )
        outcome = await self.delivery_service.deliver(
            delivery,
            kind="candidate" if soft_trigger else "reply",
            still_valid=still_valid,
            sent_reason="sent",
        )
        send_now = self.clock.now()
        if not outcome.sent:
            self._record(decision_id, topic.group_id, "END", outcome.reason, send_now)
            return outcome
        self._commit_poke_throttle(
            topic=topic,
            participation=participation,
            now=send_now,
        )
        if participation.obligation is ParticipationObligation.OPEN_OPTIONAL:
            self.budgets.record_send(send_now)
        if outcome.text:
            self._recent_outputs[topic.group_id].append(outcome.text)
        self._remember_session_turns(topic, outcome.text, send_now)
        return outcome

    def _commit_poke_throttle(
        self,
        *,
        topic: TopicSnapshot,
        participation,
        now: int,
    ) -> None:
        reasons = set(participation.reason_codes or ())
        latest = topic.latest
        if latest is None:
            return
        if "poke_direct" in reasons:
            self.participation_engine.poke_throttle.mark_direct_reacted(
                persona_id=self.persona_context.persona_id,
                group_id=topic.group_id,
                sender_id=latest.sender_id,
                now=now,
            )
            return
        if "poke_bystander" in reasons:
            self.participation_engine.poke_throttle.mark_bystander_reacted(
                persona_id=self.persona_context.persona_id,
                group_id=topic.group_id,
                now=now,
            )

    def _poke_outbound_target(
        self,
        topic: TopicSnapshot,
        participation,
        interaction,
    ) -> str:
        latest = topic.latest
        if latest is None:
            return ""
        role = self._poke_role(topic)
        if role == "bystander":
            poker_id = str(
                latest.metadata.get("poker_id", latest.sender_id) or ""
            ).strip()
            victim_id = str(latest.metadata.get("target_id", "") or "").strip()
            return self.participation_engine.poke_throttle.pick_bystander_target(
                poker_id=poker_id,
                victim_id=victim_id,
                policy=interaction,
            )
        if role == "direct" or "poke_direct" in getattr(
            participation, "reason_codes", ()
        ):
            return str(latest.sender_id or "").strip()
        return ""

    def _social_user_id(self, targeting: TargetingDecision) -> Optional[str]:
        target = targeting.social_target
        if target.kind is not AddresseeKind.USER:
            return None
        if not target.target_user_ids:
            return None
        return str(target.target_user_ids[0])

    def _relationship_state_for_target(
        self,
        topic: TopicSnapshot,
        targeting: TargetingDecision,
    ):
        if targeting.social_target.kind is AddresseeKind.AMBIGUOUS:
            return None
        user_id = self._social_user_id(targeting)
        if not user_id:
            return None
        return self.memory.get_member_relationship_state(
            self.persona_context.persona_id,
            topic.group_id,
            user_id,
            configured_relationship=self._configured_relationship_for(
                topic.group_id, user_id
            ),
            now=self.clock.now(),
        )

    def _configured_relationship_for(
        self, group_id: str, user_id: Optional[str]
    ) -> str:
        target = str(user_id or "")
        if not target:
            return ""
        member_ids = set(
            self.memory.member_subject_ids(
                self.persona_context.persona_id, group_id, target
            )
        )
        for entry in self.persona_context.relationship_seeds:
            if str(entry.sender_id) in member_ids:
                return str(entry.relationship or "")
        return ""

    def _remember_session_turns(
        self,
        topic: TopicSnapshot,
        assistant_text: str,
        timestamp: int,
    ) -> None:
        latest = topic.latest
        session = self.session_for(topic.group_id)
        cleaned = str(assistant_text or "").strip()
        if latest is not None and latest.origin is MessageOrigin.SYSTEM_SYNTHETIC:
            # Synthetic poke is not a user chat turn, but outbound poke/text must
            # remain visible so follow-ups like "你戳我干什么" can be answered.
            if cleaned:
                session.append_assistant(
                    cleaned,
                    timestamp,
                    speaker=self.character_name,
                )
            return
        if latest is not None and not latest.is_bot and latest.text:
            session.append_user(
                latest.sender_name or "群友",
                latest.text,
                latest.timestamp,
                speaker_id=latest.sender_id,
                source_message_id=latest.message_id,
            )
        if cleaned:
            session.append_assistant(
                cleaned,
                timestamp,
                speaker=self.character_name,
            )

    async def _send_copied_at_tip(
        self,
        decision_id: str,
        topic: TopicSnapshot,
        trigger_alias: str,
        now: int,
        still_valid: Optional[Callable[[], bool]] = None,
    ) -> WorkflowOutcome:
        text = copied_at_tip(trigger_alias)
        self._record(decision_id, topic.group_id, "GATE", "copied_plain_at", now)
        delivery = build_delivery_plan(
            decision_id=decision_id,
            group_id=topic.group_id,
            text=text,
            urgency=Urgency.HIGH,
            now=now,
            ttl_seconds=20,
            max_chars=max(1, len(text)),
            max_segments=1,
            humanize_delay=False,
            direct_wake=True,
        )
        outcome = await self.delivery_service.deliver(
            delivery,
            kind="copied_at_tip",
            still_valid=still_valid,
            sent_reason="copied_at_tip",
        )
        if outcome.sent:
            self._recent_outputs[topic.group_id].append(text)
        else:
            self._record(
                decision_id, topic.group_id, "END", outcome.reason, self.clock.now()
            )
        return outcome

    @staticmethod
    def _topic_image_urls(topic: TopicSnapshot) -> tuple:
        active = select_active_messages(
            topic.messages, topic_created_at=topic.created_at
        )
        urls = []
        for message in active:
            urls.extend(message.image_urls)
        return tuple(dict.fromkeys(urls))

    @staticmethod
    def _has_interleaved_context(
        topic: TopicSnapshot, target_message_id: Optional[str]
    ) -> bool:
        active = select_active_messages(
            topic.messages, topic_created_at=topic.created_at
        )
        humans = [message for message in active if not message.is_bot]
        if len(humans) < 2:
            return False
        recent = humans[-4:]
        if recent[-1].timestamp - recent[0].timestamp > 15:
            return False
        senders = {message.sender_id for message in recent if message.sender_id}
        if len(senders) > 1:
            return True
        return bool(
            target_message_id
            and recent[-1].message_id != str(target_message_id)
        )

    _SKIP_OBSERVE_CONTINUITY = frozenset(
        {
            "inhibit:owned_by_other_user",
            "inhibit:passing_alias_mention",
            "inhibit:ambiguous_target",
            "copied_at_bypassed",
            "empty_topic",
            "bypassed_trigger",
        }
    )

    async def _maybe_observe_continuity(
        self,
        topic: TopicSnapshot,
        targeting: TargetingDecision,
        *,
        decision_id: str,
        now: int,
        reason_codes: Sequence[str],
    ) -> None:
        latest = topic.latest
        if (
            latest is None
            or latest.is_bot
            or self._SKIP_OBSERVE_CONTINUITY.intersection(reason_codes)
            or not looks_like_open_loop(latest.text)
        ):
            return
        try:
            item = await self.continuity_writer.process(
                topic,
                targeting,
                decision_id=decision_id,
                now=now,
                reply_text="",
            )
        except Exception:  # noqa: BLE001 - observing must never break a turn
            self._record(
                decision_id,
                topic.group_id,
                "CONTINUITY",
                "observe_failed",
                now,
            )
            return
        if item is not None:
            self._record(
                decision_id,
                topic.group_id,
                "CONTINUITY",
                "open_on_observe:" + item.kind.value,
                now,
            )

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
        self.memory.record_transition(
            self.persona_context.persona_id,
            decision_id,
            group_id,
            state,
            reason,
            timestamp,
        )
