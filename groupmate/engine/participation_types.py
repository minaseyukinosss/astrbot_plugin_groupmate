"""Immutable contracts for the unified participation decision engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from ..core.response_act import ResponseAct
from ..models import InteractionScene, QuoteMode, StringEnum
from ..social.affinity import ResponsePosture
from .direct_pressure import DirectAddressPressureState


class ParticipationAction(StringEnum):
    """ParticipationAction（参与动作）。"""

    SPEAK = "speak"
    SILENCE = "silence"


class ParticipationObligation(StringEnum):
    """ParticipationObligation（回应义务）。"""

    DIRECT_REQUIRED = "direct_required"
    OPEN_OPTIONAL = "open_optional"
    NONE = "none"


@dataclass(frozen=True)
class MediaPolicy:
    """MediaPolicy（媒体策略）。"""

    decorative_allowed: bool = False
    visual_reaction_allowed: bool = False
    capability_media_allowed: bool = False


@dataclass(frozen=True)
class ParticipationDecision:
    """ParticipationDecision（参与决策）。"""

    action: ParticipationAction
    scene: InteractionScene
    act: Optional[ResponseAct]
    posture: ResponsePosture
    obligation: ParticipationObligation
    reason_codes: Tuple[str, ...]
    contribution: str = ""
    quote_mode: QuoteMode = QuoteMode.NEVER
    media_policy: MediaPolicy = MediaPolicy()
    pressure: Optional[DirectAddressPressureState] = None

    def __post_init__(self) -> None:
        action = self.action
        if not isinstance(action, ParticipationAction):
            action = ParticipationAction(str(action))
        obligation = self.obligation
        if not isinstance(obligation, ParticipationObligation):
            obligation = ParticipationObligation(str(obligation))
        scene = self.scene
        if not isinstance(scene, InteractionScene):
            scene = InteractionScene(str(scene))
        posture = self.posture
        if not isinstance(posture, ResponsePosture):
            posture = ResponsePosture(str(posture))
        quote_mode = self.quote_mode
        if not isinstance(quote_mode, QuoteMode):
            quote_mode = QuoteMode(str(quote_mode))
        act = self.act
        if act is not None and not isinstance(act, ResponseAct):
            act = ResponseAct(str(act))
        contribution = str(self.contribution or "").strip()
        reasons = tuple(
            str(item).strip()
            for item in (self.reason_codes or ())
            if str(item).strip()
        )
        if action is ParticipationAction.SPEAK and act is None:
            raise ValueError("speak participation decision requires response act")
        if action is ParticipationAction.SPEAK and obligation is ParticipationObligation.NONE:
            raise ValueError("speak participation decision requires obligation")
        if action is ParticipationAction.SILENCE:
            if act is not None or contribution:
                raise ValueError("silence participation decision cannot respond")
            obligation = ParticipationObligation.NONE

        object.__setattr__(self, "action", action)
        object.__setattr__(self, "scene", scene)
        object.__setattr__(self, "act", act)
        object.__setattr__(self, "posture", posture)
        object.__setattr__(self, "obligation", obligation)
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(self, "contribution", contribution)
        object.__setattr__(self, "quote_mode", quote_mode)

    @classmethod
    def speak(
        cls,
        *,
        scene: InteractionScene,
        act: ResponseAct,
        posture: ResponsePosture,
        obligation: ParticipationObligation,
        reason_codes: Tuple[str, ...],
        contribution: str,
        quote_mode: QuoteMode = QuoteMode.NEVER,
        media_policy: MediaPolicy = MediaPolicy(),
        pressure: Optional[DirectAddressPressureState] = None,
    ) -> "ParticipationDecision":
        """speak（发言决策）：构造带回应动作的参与结果。"""

        return cls(
            action=ParticipationAction.SPEAK,
            scene=scene,
            act=act,
            posture=posture,
            obligation=obligation,
            reason_codes=reason_codes,
            contribution=contribution,
            quote_mode=quote_mode,
            media_policy=media_policy,
            pressure=pressure,
        )

    @classmethod
    def silence(
        cls,
        *,
        scene: InteractionScene,
        reason_codes: Tuple[str, ...],
        posture: ResponsePosture = ResponsePosture.POLITE,
        pressure: Optional[DirectAddressPressureState] = None,
    ) -> "ParticipationDecision":
        """silence（沉默决策）：构造不产生回复的参与结果。"""

        return cls(
            action=ParticipationAction.SILENCE,
            scene=scene,
            act=None,
            posture=posture,
            obligation=ParticipationObligation.NONE,
            reason_codes=reason_codes,
            pressure=pressure,
        )
