"""Unified deterministic participation decision engine."""

from __future__ import annotations

from typing import Sequence

from ..core.intent import select_reply_mode
from ..core.response_act import ResponseAct, TaskResolution, plan_response_act
from ..core.scenes import classify_scene
from ..models import (
    GroupPolicy,
    InteractionScene,
    QuoteMode,
    TargetingDecision,
    TopicSnapshot,
    TriggerKind,
)
from ..persona.aemeath.behavior_profile import PersonaParticipationProfile
from ..social.affinity import AffinityBand, AffinitySnapshot, ResponsePosture
from .direct_pressure import (
    DirectAddressPressureLevel,
    DirectAddressPressureTracker,
)
from .participation_types import (
    MediaPolicy,
    ParticipationDecision,
    ParticipationObligation,
)

_DIRECT_TRIGGERS = frozenset(
    {
        TriggerKind.NATIVE_DIRECT,
        TriggerKind.ALIAS_DIRECT,
        TriggerKind.CONTINUATION,
    }
)


class ParticipationDecisionEngine:
    """ParticipationDecisionEngine（统一参与决策引擎）。"""

    def __init__(
        self,
        *,
        pressure: DirectAddressPressureTracker = None,
    ) -> None:
        self.pressure = pressure or DirectAddressPressureTracker()

    def decide(
        self,
        *,
        topic: TopicSnapshot,
        trigger: TriggerKind,
        policy: GroupPolicy,
        targeting: TargetingDecision,
        now: int,
        aliases: Sequence[str],
        affinity: AffinitySnapshot,
        persona: PersonaParticipationProfile,
        recent_outputs: Sequence[str],
        task_resolution: TaskResolution = None,
    ) -> ParticipationDecision:
        """decide（参与决策）：一次性决定发言、动作和姿态。"""

        del policy, recent_outputs
        latest = topic.latest
        if latest is None:
            return ParticipationDecision.silence(
                scene=InteractionScene.AMBIENT_CONTRIBUTION,
                reason_codes=("empty_topic",),
            )
        if trigger is TriggerKind.COPIED_AT:
            return ParticipationDecision.silence(
                scene=InteractionScene.AMBIENT_CONTRIBUTION,
                reason_codes=("copied_at_bypassed",),
                posture=affinity.response_posture,
            )
        scene = classify_scene(trigger, latest)
        if trigger in (TriggerKind.IGNORE, TriggerKind.COMMAND):
            return ParticipationDecision.silence(
                scene=scene,
                reason_codes=("bypassed_trigger", trigger.value),
                posture=affinity.response_posture,
            )
        if trigger in _DIRECT_TRIGGERS:
            return self._direct_decision(
                topic=topic,
                trigger=trigger,
                targeting=targeting,
                now=now,
                aliases=aliases,
                affinity=affinity,
                persona=persona,
                task_resolution=task_resolution,
            )
        return ParticipationDecision.silence(
            scene=scene,
            reason_codes=("open_participation_not_implemented",),
            posture=affinity.response_posture,
        )

    def _direct_decision(
        self,
        *,
        topic: TopicSnapshot,
        trigger: TriggerKind,
        targeting: TargetingDecision,
        now: int,
        aliases: Sequence[str],
        affinity: AffinitySnapshot,
        persona: PersonaParticipationProfile,
        task_resolution: TaskResolution = None,
    ) -> ParticipationDecision:
        latest = topic.latest
        if latest is None:
            raise ValueError("direct participation requires latest message")
        persona.rule_for_affinity(affinity.band)
        pressure = self.pressure.observe(
            latest,
            trigger,
            now=now,
            aliases=aliases,
        )
        mode = select_reply_mode(latest.text, soft_trigger=False)
        act_plan = plan_response_act(
            classify_scene(trigger, latest),
            reply_mode=mode,
            text=latest.text,
            aliases=aliases,
            has_visual=bool(latest.image_urls),
            task_resolution=task_resolution or TaskResolution(),
        )
        act = self._pressure_act(
            act_plan.act,
            pressure.level,
            affinity.band,
        )
        posture = self._pressure_posture(
            affinity.response_posture,
            pressure.level,
            affinity.band,
        )
        reasons = (
            ("direct_required",)
            + tuple(act_plan.reason_codes)
            + tuple(pressure.reason_codes)
        )
        ambiguous = bool(
            targeting.reply_audience.kind.value == "ambiguous"
            or targeting.social_target.kind.value == "ambiguous"
        )
        return ParticipationDecision.speak(
            scene=act_plan.scene,
            act=act,
            posture=posture,
            obligation=ParticipationObligation.DIRECT_REQUIRED,
            reason_codes=reasons,
            contribution=self._contribution_for_act(act),
            quote_mode=self._quote_mode(trigger, latest.reply_to_bot),
            media_policy=self._media_policy(act, ambiguous),
            pressure=pressure,
        )

    @staticmethod
    def _pressure_act(
        base: ResponseAct,
        level: DirectAddressPressureLevel,
        band: AffinityBand,
    ) -> ResponseAct:
        if level in (
            DirectAddressPressureLevel.PESTER,
            DirectAddressPressureLevel.AFTER_BOUNDARY,
        ) and band in (AffinityBand.HOSTILE, AffinityBand.WARY):
            return ResponseAct.BOUNDARY
        if level in (
            DirectAddressPressureLevel.NUDGE,
            DirectAddressPressureLevel.PESTER,
        ) and band in (AffinityBand.FRIENDLY, AffinityBand.CLOSE):
            return ResponseAct.PLAYFUL_REPLY
        return base

    @staticmethod
    def _pressure_posture(
        base: ResponsePosture,
        level: DirectAddressPressureLevel,
        band: AffinityBand,
    ) -> ResponsePosture:
        if level in (
            DirectAddressPressureLevel.PESTER,
            DirectAddressPressureLevel.AFTER_BOUNDARY,
        ):
            if band is AffinityBand.HOSTILE:
                return ResponsePosture.FIRM
            if band is AffinityBand.WARY:
                return ResponsePosture.RESERVED
        return base

    @staticmethod
    def _quote_mode(trigger: TriggerKind, reply_to_bot: bool) -> QuoteMode:
        if reply_to_bot:
            return QuoteMode.ALWAYS
        if trigger is TriggerKind.CONTINUATION:
            return QuoteMode.WHEN_INTERLEAVED
        return QuoteMode.NEVER

    @staticmethod
    def _media_policy(act: ResponseAct, ambiguous: bool) -> MediaPolicy:
        if ambiguous or act is ResponseAct.BOUNDARY:
            return MediaPolicy()
        return MediaPolicy(
            decorative_allowed=act
            in (ResponseAct.RECIPROCATE, ResponseAct.PLAYFUL_REPLY),
            visual_reaction_allowed=act is ResponseAct.VISUAL_REACTION,
            capability_media_allowed=act is ResponseAct.TASK_HANDOFF,
        )

    @staticmethod
    def _contribution_for_act(act: ResponseAct) -> str:
        instructions = {
            ResponseAct.ACKNOWLEDGE: "短应声，不主动扩展话题",
            ResponseAct.ANSWER: "给出与问题直接相关的短答",
            ResponseAct.CLARIFY: "只追问完成任务所缺的信息",
            ResponseAct.RECIPROCATE: "自然回应对方的善意",
            ResponseAct.PLAYFUL_REPLY: (
                "用爱弥斯风格轻轻戏谑一下，让对方说正事"
            ),
            ResponseAct.BOUNDARY: "短句守住边界，不延长空 @",
            ResponseAct.TASK_HANDOFF: "如实说明任务正在交接，不声称完成",
            ResponseAct.TASK_UNSUPPORTED: "简短说明当前无法完成这项任务",
            ResponseAct.VISUAL_REACTION: "针对视觉内容给一句相关反应",
        }
        return instructions[act]
