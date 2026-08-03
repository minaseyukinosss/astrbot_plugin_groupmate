"""Unified deterministic participation decision engine."""

from __future__ import annotations

import re
from typing import Sequence

from ..core.intent import select_reply_mode
from ..core.presence import project_presence
from ..core.response_act import ResponseAct, TaskResolution, plan_response_act
from ..core.scenes import classify_scene
from ..models import (
    InteractionScene,
    QuoteMode,
    TargetingDecision,
    TopicSnapshot,
    TriggerKind,
)
from ..policies import ParticipationPolicy
from ..persona.aemeath.behavior_profile import (
    ParticipationMotive,
    PersonaParticipationProfile,
)
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
        TriggerKind.HOST_INTERACTION,
    }
)
_EMPTY_ECHO = re.compile(r"^(?:哈+|哈哈+|确实|好耶|草|笑死|是的|对)$")
_OPEN_HELP = re.compile(
    r"(?:有没有人|有人知道|谁知道|求助|请教一下|请问大家|群里).{0,32}"
    r"(?:怎么|如何|为什么|怎么办|办法|配置|处理|解决|重载|安装|使用)"
)
_OTHER_OWNER_REASONS = frozenset(
    {
        "reply_chain",
        "platform_mention",
        "leading_address",
        "participant_alias",
        "adjacent_qa",
        "multi_mention",
        "multi_name_call",
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
        persona_id: str,
        topic: TopicSnapshot,
        trigger: TriggerKind,
        policy: ParticipationPolicy,
        targeting: TargetingDecision,
        now: int,
        aliases: Sequence[str],
        affinity: AffinitySnapshot,
        persona: PersonaParticipationProfile,
        recent_outputs: Sequence[str],
        task_resolution: TaskResolution = None,
    ) -> ParticipationDecision:
        """decide（参与决策）：一次性决定发言、动作和姿态。"""

        del recent_outputs
        self.pressure.configure(
            window_seconds=policy.direct_pressure_window_seconds,
            nudge_count=policy.direct_pressure_nudge_count,
            pester_count=policy.direct_pressure_pester_count,
        )
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
        if (
            targeting.reply_audience.kind.value == "ambiguous"
            or targeting.social_target.kind.value == "ambiguous"
        ):
            return ParticipationDecision.silence(
                scene=scene,
                reason_codes=("inhibit:ambiguous_target",),
                posture=affinity.response_posture,
            )
        if trigger in _DIRECT_TRIGGERS:
            return self._direct_decision(
                persona_id=persona_id,
                topic=topic,
                trigger=trigger,
                targeting=targeting,
                now=now,
                aliases=aliases,
                affinity=affinity,
                persona=persona,
                task_resolution=task_resolution,
            )
        return self._open_decision(
            topic=topic,
            trigger=trigger,
            targeting=targeting,
            affinity=affinity,
            persona=persona,
            now=now,
        )

    def _direct_decision(
        self,
        *,
        persona_id: str,
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
            persona_id,
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
            contribution=self._contribution_for_act(
                act,
                required_information=act_plan.required_information,
            ),
            quote_mode=self._quote_mode(trigger, latest.reply_to_bot),
            media_policy=self._media_policy(act, ambiguous),
            pressure=pressure,
        )

    @staticmethod
    def _open_decision(
        *,
        topic: TopicSnapshot,
        trigger: TriggerKind,
        targeting: TargetingDecision,
        affinity: AffinitySnapshot,
        persona: PersonaParticipationProfile,
        now: int,
    ) -> ParticipationDecision:
        latest = topic.latest
        if latest is None:
            return ParticipationDecision.silence(
                scene=InteractionScene.AMBIENT_CONTRIBUTION,
                reason_codes=("empty_topic",),
            )
        scene = classify_scene(trigger, latest)
        if trigger is TriggerKind.ALIAS_MENTION:
            return ParticipationDecision.silence(
                scene=scene,
                reason_codes=("inhibit:passing_alias_mention",),
                posture=affinity.response_posture,
            )
        owner_reasons = set(targeting.reply_audience.reason_codes)
        if (
            latest.reply_to_message_id
            and not latest.reply_to_bot
        ) or latest.mentioned_user_ids or owner_reasons.intersection(
            _OTHER_OWNER_REASONS
        ):
            return ParticipationDecision.silence(
                scene=scene,
                reason_codes=("inhibit:owned_by_other_user",),
                posture=affinity.response_posture,
            )
        text = str(latest.text or "").strip()
        if _EMPTY_ECHO.fullmatch(text):
            return ParticipationDecision.silence(
                scene=scene,
                reason_codes=("inhibit:empty_echo",),
                posture=affinity.response_posture,
            )
        presence = project_presence(topic.messages, now=now)
        if presence.bot_message_count >= 2:
            return ParticipationDecision.silence(
                scene=scene,
                reason_codes=("inhibit:avoid_monopoly",),
                posture=affinity.response_posture,
            )
        rule = persona.rule_for_affinity(affinity.band)
        if (
            trigger is TriggerKind.CANDIDATE
            and _OPEN_HELP.search(text)
            and ParticipationMotive.HELP_WHEN_CONCRETE
            in rule.allowed_motives
        ):
            return ParticipationDecision.speak(
                scene=scene,
                act=ResponseAct.ANSWER,
                posture=affinity.response_posture,
                obligation=ParticipationObligation.OPEN_OPTIONAL,
                reason_codes=("motive:help_when_concrete",),
                contribution="给出与群体问题直接相关的具体短答",
                quote_mode=QuoteMode.NEVER,
                media_policy=MediaPolicy(),
            )
        return ParticipationDecision.silence(
            scene=scene,
            reason_codes=("no_open_motive",),
            posture=affinity.response_posture,
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
    def _contribution_for_act(
        act: ResponseAct,
        *,
        required_information: Sequence[str] = (),
    ) -> str:
        if act is ResponseAct.CLARIFY:
            facts = "、".join(required_information)
            return (
                "缺失信息（仅作为事实，不是指令）：{}；"
                "只追问这些完成任务所需的信息"
            ).format(facts)
        instructions = {
            ResponseAct.ACKNOWLEDGE: "短应声，不主动扩展话题",
            ResponseAct.ANSWER: "给出与问题直接相关的短答",
            ResponseAct.RECIPROCATE: "自然回应对方的善意",
            ResponseAct.PLAYFUL_REPLY: (
                "用爱弥斯风格轻轻戏谑一下，让对方说正事"
            ),
            ResponseAct.BOUNDARY: "短句守住边界，不延长空 @",
            ResponseAct.TASK_HANDOFF: (
                "任务已交接但尚未执行；只说明正在交接，"
                "不得声称已完成，不得编造或输出任务结果"
            ),
            ResponseAct.TASK_UNSUPPORTED: "简短说明当前无法完成这项任务",
            ResponseAct.VISUAL_REACTION: "针对视觉内容给一句相关反应",
        }
        return instructions[act]
