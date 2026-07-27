"""OpportunityArbiter：确定性 soft prefilter + 可解释 utility。"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Optional, Sequence
from uuid import uuid4

from ..core.presence import PresenceProjection, project_presence
from ..models import (
    AddresseeKind,
    GroupPolicy,
    OpportunityAction,
    SpeakOpportunity,
    TargetingDecision,
    TopicSnapshot,
    TriggerKind,
)
from .rate_limit import BudgetTracker, SlidingWindowRateLimiter
from .topics import select_active_messages

UTILITY_THRESHOLD = 0.45

_HARD = frozenset(
    {
        TriggerKind.NATIVE_DIRECT,
        TriggerKind.ALIAS_DIRECT,
        TriggerKind.CONTINUATION,
    }
)
_SOFT = frozenset({TriggerKind.ALIAS_MENTION, TriggerKind.CANDIDATE})

_QUESTION = re.compile(r"[？?]|吗$|呢$|怎么|什么|谁|哪|帮")
_SPAM_REPEAT = re.compile(r"(.)\1{5,}")


class OpportunityArbiter:
    def __init__(
        self,
        *,
        threshold: float = UTILITY_THRESHOLD,
        budgets: Optional[BudgetTracker] = None,
        send_limiter: Optional[SlidingWindowRateLimiter] = None,
    ) -> None:
        self.threshold = float(threshold)
        if budgets is not None:
            self.budgets = budgets
        else:
            limiter = send_limiter or SlidingWindowRateLimiter()
            self.budgets = BudgetTracker(limiter)

    def evaluate(
        self,
        topic: TopicSnapshot,
        trigger: TriggerKind,
        policy: GroupPolicy,
        targeting: TargetingDecision,
        *,
        now: int,
        recent_outputs: Sequence[str] = (),
        hard_busy: bool = False,
        favorability: Optional[int] = None,
    ) -> SpeakOpportunity:
        active = select_active_messages(
            topic.messages, topic_created_at=topic.created_at
        )
        latest = active[-1] if active else topic.latest
        audience = self._audience_ids(targeting)
        target_mid = (
            targeting.reply_audience.target_message_id
            or (latest.message_id if latest else None)
        )
        expires_at = int(now) + max(1, int(policy.candidate_ttl_seconds))

        if trigger in _HARD:
            return SpeakOpportunity(
                opportunity_id=uuid4().hex,
                group_id=topic.group_id,
                action=OpportunityAction.SPEAK,
                trigger=trigger,
                audience_ids=audience,
                target_message_id=target_mid,
                contribution=self._hard_contribution(trigger),
                confidence=1.0,
                interruption_cost=0.0,
                created_at=int(now),
                expires_at=expires_at,
                reason_codes=("hard_trigger", trigger.value),
            )

        prefilter = self._prefilter(
            topic,
            trigger,
            policy,
            active,
            latest,
            now=now,
            hard_busy=hard_busy,
        )
        if prefilter:
            return SpeakOpportunity(
                opportunity_id=uuid4().hex,
                group_id=topic.group_id,
                action=OpportunityAction.SILENCE,
                trigger=trigger,
                audience_ids=audience,
                target_message_id=target_mid,
                contribution="",
                confidence=0.0,
                interruption_cost=1.0,
                created_at=int(now),
                expires_at=expires_at,
                reason_codes=("prefilter", prefilter),
            )

        presence = project_presence(active, now=now)
        utility, parts, interruption = self._score_utility(
            trigger=trigger,
            targeting=targeting,
            latest_text=(latest.text if latest else ""),
            presence=presence,
            recent_outputs=recent_outputs,
            favorability=favorability,
            aliases=policy.aliases,
        )
        reason_codes = tuple(parts) + (
            "utility={:.2f}".format(utility),
            "threshold={:.2f}".format(self.threshold),
        )
        if utility < self.threshold:
            return SpeakOpportunity(
                opportunity_id=uuid4().hex,
                group_id=topic.group_id,
                action=OpportunityAction.SILENCE,
                trigger=trigger,
                audience_ids=audience,
                target_message_id=target_mid,
                contribution="",
                confidence=max(0.0, utility),
                interruption_cost=interruption,
                created_at=int(now),
                expires_at=expires_at,
                reason_codes=("opportunity_silence",) + reason_codes,
            )
        return SpeakOpportunity(
            opportunity_id=uuid4().hex,
            group_id=topic.group_id,
            action=OpportunityAction.SPEAK,
            trigger=trigger,
            audience_ids=audience,
            target_message_id=target_mid,
            contribution=self._soft_contribution(trigger, latest.text if latest else ""),
            confidence=min(1.0, utility),
            interruption_cost=interruption,
            created_at=int(now),
            expires_at=expires_at,
            reason_codes=("opportunity_speak",) + reason_codes,
        )

    def _prefilter(
        self,
        topic: TopicSnapshot,
        trigger: TriggerKind,
        policy: GroupPolicy,
        active,
        latest,
        *,
        now: int,
        hard_busy: bool,
    ) -> str:
        if hard_busy:
            return "hard_busy"
        if trigger in (TriggerKind.IGNORE, TriggerKind.COMMAND):
            return "bypassed_trigger"
        if not topic.messages:
            return "empty_topic"
        if latest is None:
            return "empty_topic"
        if latest.is_bot:
            return "bot_echo"
        if not latest.has_content:
            return "empty_content"
        text = (latest.text or "").strip()
        if not text and not latest.image_urls:
            return "no_recognizable_content"
        if _SPAM_REPEAT.search(text.replace(" ", "")):
            return "spam_repeat"
        if (
            trigger in _SOFT
            and now - topic.updated_at > policy.candidate_ttl_seconds
        ):
            return "stale_topic"
        # 短窗刷屏：连续两条几乎相同人类消息
        humans = [m for m in active if not m.is_bot]
        if len(humans) >= 2:
            a, b = humans[-1].text.strip(), humans[-2].text.strip()
            if a and b and SequenceMatcher(None, a, b).ratio() >= 0.92:
                return "rapid_duplicate"
        if trigger is TriggerKind.CANDIDATE and not self.budgets.allow_send(now):
            return "send_budget_exhausted"
        if not self.budgets.allow_generation(now):
            return "generation_budget_exhausted"
        return ""

    def _score_utility(
        self,
        *,
        trigger: TriggerKind,
        targeting: TargetingDecision,
        latest_text: str,
        presence: PresenceProjection,
        recent_outputs: Sequence[str],
        favorability: Optional[int],
        aliases: Sequence[str],
    ):
        parts = []
        addressedness = 0.0
        if trigger is TriggerKind.ALIAS_MENTION:
            addressedness = 0.35
            parts.append("addressedness=0.35")
        elif trigger is TriggerKind.CANDIDATE:
            addressedness = 0.1
            parts.append("addressedness=0.10")
        if targeting.reply_audience.kind is AddresseeKind.USER:
            addressedness += 0.1
            parts.append("address_user+0.10")

        contribution_value = 0.15
        if _QUESTION.search(latest_text or ""):
            contribution_value = 0.4
            parts.append("contribution=0.40")
        else:
            parts.append("contribution=0.15")

        topic_relevance = 0.15
        for alias in aliases:
            if alias and alias in (latest_text or ""):
                topic_relevance = 0.25
                break
        parts.append("topic={:.2f}".format(topic_relevance))

        relationship_relevance = 0.05
        if favorability is not None and favorability >= 50:
            relationship_relevance = 0.15
        parts.append("rel={:.2f}".format(relationship_relevance))

        novelty = 0.2
        for prev in recent_outputs:
            if SequenceMatcher(None, latest_text or "", prev or "").ratio() >= 0.75:
                novelty = 0.0
                break
        parts.append("novelty={:.2f}".format(novelty))

        interruption_cost = 0.1 + 0.5 * presence.recent_bot_density
        if presence.human_turn_gap < 8:
            interruption_cost += 0.15
        parts.append("interrupt=-{:.2f}".format(interruption_cost))

        density_penalty = 0.4 * presence.recent_bot_density
        parts.append("density=-{:.2f}".format(density_penalty))

        duplication_risk = 0.0
        for prev in recent_outputs[-3:]:
            if SequenceMatcher(None, (latest_text or "")[:40], (prev or "")[:40]).ratio() >= 0.8:
                duplication_risk = 0.35
                break
        parts.append("dup=-{:.2f}".format(duplication_risk))

        ambiguity_risk = 0.0
        if targeting.social_target.kind is AddresseeKind.AMBIGUOUS:
            ambiguity_risk = 0.35
        elif targeting.reply_audience.kind is AddresseeKind.AMBIGUOUS:
            ambiguity_risk = 0.25
        parts.append("amb=-{:.2f}".format(ambiguity_risk))

        utility = (
            addressedness
            + contribution_value
            + topic_relevance
            + relationship_relevance
            + novelty
            - interruption_cost
            - density_penalty
            - duplication_risk
            - ambiguity_risk
        )
        return utility, parts, interruption_cost

    @staticmethod
    def _audience_ids(targeting: TargetingDecision):
        audience = targeting.reply_audience
        if audience.kind is AddresseeKind.USER and audience.target_user_ids:
            return tuple(audience.target_user_ids)
        return ()

    @staticmethod
    def _hard_contribution(trigger: TriggerKind) -> str:
        if trigger is TriggerKind.CONTINUATION:
            return "继续回应对方刚才的对话"
        if trigger is TriggerKind.NATIVE_DIRECT:
            return "回应对方刚才的直接呼叫"
        return "回应对方刚才的直接呼叫"

    @staticmethod
    def _soft_contribution(trigger: TriggerKind, text: str) -> str:
        if _QUESTION.search(text or ""):
            return "若问题冲你且你能给一句有用短答就接；否则沉默"
        if trigger is TriggerKind.ALIAS_MENTION:
            return "若被点到名字且有一句自然短反应就接；否则沉默"
        return "若话冲你且有一句自然短反应，就接一下；否则沉默"
