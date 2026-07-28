"""Side-effect-free projection through current Groupmate mechanics."""

from __future__ import annotations

from dataclasses import replace

from eval.shadow_extract import LocalIdHasher, normalize_alias
from eval.shadow_models import BehaviorExample, ShadowProjection
from groupmate.core.addressee import AddresseeResolver
from groupmate.core.response_act import TaskResolution, TaskResolutionStatus
from groupmate.core.scenes import classify_scene, policy_for_scene
from groupmate.engine.external_knowledge import needs_external_knowledge
from groupmate.engine.opportunity import OpportunityArbiter
from groupmate.engine.planner import ReplyIntentPlanner
from groupmate.engine.rate_limit import SlidingWindowRateLimiter
from groupmate.engine.topics import select_active_messages
from groupmate.engine.triggers import TriggerRouter
from groupmate.media.reactions import ReactionPolicy
from groupmate.models import (
    AddresseeKind,
    ChatMessage,
    GroupPolicy,
    InteractionScene,
    OpportunityAction,
    TopicSnapshot,
    TriggerKind,
)
from groupmate.core.response_act import ResponseAct


class ShadowProjector:
    def __init__(
        self,
        policy: GroupPolicy,
        hasher: LocalIdHasher,
        *,
        target_uin: str,
        target_alias: str,
        current_alias: str,
    ) -> None:
        if not isinstance(policy, GroupPolicy):
            raise TypeError("policy must be a GroupPolicy")
        if not isinstance(hasher, LocalIdHasher):
            raise TypeError("hasher must be a LocalIdHasher")
        target = str(target_uin or "").strip()
        old_alias = str(target_alias or "").strip()
        alias = str(current_alias or "").strip()
        if not target or not old_alias or not alias:
            raise ValueError("target UIN and aliases are required")
        self.policy = replace(policy, aliases=(alias,))
        self.hasher = hasher
        self.target_uin = target
        self.target_alias = old_alias
        self.current_alias = alias
        self.addressee = AddresseeResolver()
        self.arbiter = OpportunityArbiter(
            send_limiter=SlidingWindowRateLimiter(
                hourly_limit=max(100000, policy.spontaneous_hourly_limit),
                cooldown_seconds=0,
            )
        )
        self.planner = ReplyIntentPlanner()
        self.reactions = ReactionPolicy()

    def project(self, example: BehaviorExample) -> ShadowProjection:
        if not isinstance(example, BehaviorExample):
            raise TypeError("example must be a BehaviorExample")
        topic = self._topic(example)
        latest = topic.latest
        if latest is None:
            raise ValueError("behavior example context must not be empty")
        trigger = TriggerRouter(self.policy).classify(latest)
        if (
            trigger.kind is TriggerKind.NATIVE_DIRECT
            and needs_external_knowledge(latest.text)
        ):
            return ShadowProjection(
                sample_id=example.sample_id,
                owner="astrbot_agent",
                would_reply=True,
                trigger=trigger.kind.value,
                scene=InteractionScene.TASK_REQUEST,
                act=ResponseAct.TASK_HANDOFF,
                quote_allowed=True,
                decorative_media_allowed=False,
                capability_media_allowed=False,
                ambiguous_target=False,
                owner_count=1,
                completion_claim_allowed=False,
                reason_codes=("external_handoff",),
            )

        scene = classify_scene(trigger.kind, latest)
        targeting = self.addressee.resolve(
            topic,
            trigger.kind,
            aliases=self.policy.aliases,
            bot_id="__target_bot__",
            relationships={},
        )
        opportunity = self.arbiter.evaluate(
            topic,
            trigger.kind,
            self.policy,
            targeting,
            now=latest.timestamp,
            recent_outputs=(),
            favorability=None,
        )
        task_resolution = self._task_resolution(scene, latest)
        intent = self.planner.plan(
            opportunity,
            topic,
            targeting,
            decision_id=example.sample_id,
            scene=scene,
            aliases=self.policy.aliases,
            task_resolution=task_resolution,
        )
        act = intent.response_act.act if intent and intent.response_act else None
        ambiguous = bool(
            targeting.reply_audience.kind is AddresseeKind.AMBIGUOUS
            or targeting.social_target.kind is AddresseeKind.AMBIGUOUS
        )
        quote_allowed = policy_for_scene(scene).should_quote(
            interleaved=self._interleaved(topic, opportunity.target_message_id)
        )
        decorative = bool(
            act is not None and self.reactions.allowed(act, scene, ambiguous)
        )
        would_reply = opportunity.action is OpportunityAction.SPEAK
        reasons = tuple(opportunity.reason_codes)
        if needs_external_knowledge(latest.text):
            reasons += ("external_knowledge_groupmate_owned",)
        return ShadowProjection(
            sample_id=example.sample_id,
            owner=("groupmate" if would_reply else "observe_only"),
            would_reply=would_reply,
            trigger=trigger.kind.value,
            scene=scene,
            act=act,
            quote_allowed=quote_allowed,
            decorative_media_allowed=decorative,
            capability_media_allowed=False,
            ambiguous_target=ambiguous,
            owner_count=1,
            completion_claim_allowed=False,
            reason_codes=reasons,
        )

    def _topic(self, example: BehaviorExample) -> TopicSnapshot:
        target_message_ids = {
            item.message_id
            for item in example.context
            if item.sender_uin == self.target_uin
        }
        messages = []
        for item in example.context:
            is_target = item.sender_uin == self.target_uin
            mentions_bot = self.target_uin in item.mentions
            mentioned_users = tuple(
                self.hasher.sender_id(value)
                for value in item.mentions
                if value != self.target_uin
            )
            messages.append(ChatMessage(
                message_id=item.message_id,
                group_id="shadow-group",
                sender_id=(
                    "__target_bot__"
                    if is_target else self.hasher.sender_id(item.sender_key)
                ),
                sender_name=item.sender_name,
                text=normalize_alias(
                    item.text, self.target_alias, self.current_alias
                ),
                timestamp=max(0, int(item.timestamp_ms // 1000)),
                reply_to_message_id=(item.reply_to_message_id or None),
                reply_to_bot=bool(
                    item.reply_to_sender_uin == self.target_uin
                    or item.reply_to_message_id in target_message_ids
                ),
                mentions_bot=mentions_bot,
                is_bot=is_target,
                image_urls=(("shadow://media",) if item.has_media else ()),
                segment_types=item.element_types,
                bot_id="__target_bot__",
                mentioned_user_ids=mentioned_users,
            ))
        if not messages:
            raise ValueError("behavior example context must not be empty")
        return TopicSnapshot(
            topic_id="shadow-" + example.sample_id,
            group_id="shadow-group",
            messages=tuple(messages),
            created_at=messages[0].timestamp,
            updated_at=messages[-1].timestamp,
        )

    def _task_resolution(
        self, scene: InteractionScene, latest: ChatMessage
    ) -> TaskResolution:
        if (
            scene is InteractionScene.TASK_REQUEST
            and latest.image_urls
            and self.policy.vision_enabled
        ):
            return TaskResolution(
                TaskResolutionStatus.SUPPORTED,
                capability_name="vision",
            )
        if scene is InteractionScene.TASK_REQUEST:
            return TaskResolution(TaskResolutionStatus.UNSUPPORTED)
        return TaskResolution()

    @staticmethod
    def _interleaved(topic: TopicSnapshot, target_message_id) -> bool:
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
