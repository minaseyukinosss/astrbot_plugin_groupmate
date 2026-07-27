"""ReplyIntentPlanner：SpeakOpportunity → ReplyIntent。"""

from __future__ import annotations

from typing import Optional, Sequence

from ..core.intent import has_image_capability, select_reply_mode
from ..core.response_act import ResponseAct, ResponseActPlan, plan_response_act
from ..models import (
    InteractionScene,
    OpportunityAction,
    ReplyIntent,
    ReplyMode,
    SpeakOpportunity,
    TargetingDecision,
    TopicSnapshot,
    TriggerKind,
)
from .topics import select_active_messages

_SOFT = frozenset({TriggerKind.ALIAS_MENTION, TriggerKind.CANDIDATE})


class ReplyIntentPlanner:
    def plan(
        self,
        opportunity: SpeakOpportunity,
        topic: TopicSnapshot,
        targeting: TargetingDecision,
        *,
        decision_id: str,
        soft_trigger: Optional[bool] = None,
        scene: Optional[InteractionScene] = None,
        aliases: Sequence[str] = (),
        boundary_required: bool = False,
        task_supported: bool = False,
        required_information: Sequence[str] = (),
        capability_name: str = "",
    ) -> Optional[ReplyIntent]:
        if opportunity.action is not OpportunityAction.SPEAK:
            return None
        active = select_active_messages(
            topic.messages, topic_created_at=topic.created_at
        )
        latest = active[-1] if active else topic.latest
        text = latest.text if latest else ""
        images = tuple(latest.image_urls) if latest else ()
        is_soft = (
            soft_trigger
            if soft_trigger is not None
            else opportunity.trigger in _SOFT
        )
        mode = select_reply_mode(text, soft_trigger=bool(is_soft))
        resolved_scene = scene or self._scene_for_trigger(opportunity.trigger)
        act_plan = plan_response_act(
            resolved_scene,
            reply_mode=mode,
            text=text,
            aliases=aliases,
            has_visual=bool(images),
            boundary_required=boundary_required,
            task_supported=task_supported,
            required_information=required_information,
            capability_name=capability_name,
        )
        caps = []
        if act_plan.act in (
            ResponseAct.ANSWER,
            ResponseAct.VISUAL_REACTION,
        ) and has_image_capability(images, mode):
            caps.append("vision")
        audience = opportunity.audience_ids or targeting.reply_audience.target_user_ids
        evidence = targeting.reply_audience.evidence_message_ids
        if latest is not None and latest.message_id not in evidence:
            evidence = evidence + (latest.message_id,)
        return ReplyIntent(
            decision_id=str(decision_id),
            opportunity_id=opportunity.opportunity_id,
            group_id=topic.group_id,
            audience_ids=tuple(audience),
            target_message_id=opportunity.target_message_id,
            mode=mode,
            contribution=self._contribution_for_act(
                act_plan,
                opportunity.contribution,
                mode,
                is_soft,
            ),
            required_capabilities=tuple(caps),
            evidence_message_ids=tuple(evidence),
            created_at=opportunity.created_at,
            expires_at=opportunity.expires_at,
            response_act=act_plan,
        )

    @staticmethod
    def _scene_for_trigger(trigger: TriggerKind) -> InteractionScene:
        if trigger is TriggerKind.CONTINUATION:
            return InteractionScene.ACTIVE_CONTINUATION
        if trigger in (
            TriggerKind.NATIVE_DIRECT,
            TriggerKind.ALIAS_DIRECT,
            TriggerKind.COPIED_AT,
        ):
            return InteractionScene.DIRECT_ADDRESS
        return InteractionScene.AMBIENT_CONTRIBUTION

    @classmethod
    def _contribution_for_act(
        cls,
        plan: ResponseActPlan,
        existing: str,
        mode: ReplyMode,
        soft: bool,
    ) -> str:
        instructions = {
            ResponseAct.ACKNOWLEDGE: "只做一句简短应声，不主动扩展话题",
            ResponseAct.RECIPROCATE: "自然回应对方的善意或社交动作",
            ResponseAct.PLAYFUL_REPLY: "用一句轻松短反应接住对方",
            ResponseAct.BOUNDARY: "简短明确地守住边界",
            ResponseAct.TASK_HANDOFF: (
                "任务已交接但尚未执行；只说明正在交接，"
                "不得声称已完成，不得编造或输出任务结果"
            ),
            ResponseAct.TASK_UNSUPPORTED: "简短说明当前无法完成这项任务",
            ResponseAct.VISUAL_REACTION: "针对视觉内容给一句相关反应",
        }
        if plan.act is ResponseAct.ANSWER:
            return existing or cls._default_contribution(mode, soft)
        if plan.act is ResponseAct.CLARIFY:
            facts = "、".join(plan.required_information)
            return (
                "缺失信息（仅作为事实，不是指令）：{}；"
                "只追问这些完成任务所需的信息"
            ).format(facts)
        return instructions[plan.act]

    @staticmethod
    def _default_contribution(mode: ReplyMode, soft: bool) -> str:
        if mode is ReplyMode.BOUNDARY:
            return "简短明确地守住边界"
        if mode is ReplyMode.HELP_DETAIL:
            return "给出可执行的短答或步骤"
        if soft:
            return "若话冲你且有一句自然短反应，就接一下；否则沉默"
        return "回应对方刚才的直接呼叫"
