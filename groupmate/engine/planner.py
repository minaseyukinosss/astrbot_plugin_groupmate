"""ReplyIntentPlanner：SpeakOpportunity → ReplyIntent。"""

from __future__ import annotations

from typing import Optional, Sequence

from ..core.intent import has_image_capability, select_reply_mode
from ..models import (
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
        caps = []
        if has_image_capability(images, mode):
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
            contribution=opportunity.contribution
            or self._default_contribution(mode, is_soft),
            required_capabilities=tuple(caps),
            evidence_message_ids=tuple(evidence),
            created_at=opportunity.created_at,
            expires_at=opportunity.expires_at,
        )

    @staticmethod
    def _default_contribution(mode: ReplyMode, soft: bool) -> str:
        if mode is ReplyMode.BOUNDARY:
            return "简短明确地守住边界"
        if mode is ReplyMode.HELP_DETAIL:
            return "给出可执行的短答或步骤"
        if soft:
            return "若话冲你且有一句自然短反应，就接一下；否则沉默"
        return "回应对方刚才的直接呼叫"
