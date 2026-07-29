import pytest

from groupmate.core.response_act import ResponseAct
from groupmate.engine.direct_pressure import (
    DirectAddressPressureLevel,
    DirectAddressPressureState,
)
from groupmate.engine.participation_types import (
    MediaPolicy,
    ParticipationAction,
    ParticipationDecision,
    ParticipationObligation,
)
from groupmate.models import InteractionScene, QuoteMode
from groupmate.social.affinity import ResponsePosture


def test_speak_decision_normalizes_reason_codes_and_defaults():
    decision = ParticipationDecision.speak(
        scene=InteractionScene.DIRECT_ADDRESS,
        act=ResponseAct.ACKNOWLEDGE,
        posture=ResponsePosture.POLITE,
        obligation=ParticipationObligation.DIRECT_REQUIRED,
        reason_codes=("direct", "bare"),
        contribution="短应声",
    )

    assert decision.action is ParticipationAction.SPEAK
    assert decision.reason_codes == ("direct", "bare")
    assert decision.quote_mode is QuoteMode.NEVER
    assert decision.media_policy == MediaPolicy()


def test_silence_decision_has_no_response_act_or_contribution():
    pressure = DirectAddressPressureState(
        DirectAddressPressureLevel.NUDGE,
        2,
    )
    decision = ParticipationDecision.silence(
        scene=InteractionScene.AMBIENT_CONTRIBUTION,
        reason_codes=("empty_echo",),
        pressure=pressure,
    )

    assert decision.action is ParticipationAction.SILENCE
    assert decision.act is None
    assert decision.contribution == ""
    assert decision.obligation is ParticipationObligation.NONE
    assert decision.pressure is pressure


def test_speak_decision_requires_response_act():
    with pytest.raises(ValueError, match="response act"):
        ParticipationDecision(
            action=ParticipationAction.SPEAK,
            scene=InteractionScene.DIRECT_ADDRESS,
            act=None,
            posture=ResponsePosture.POLITE,
            obligation=ParticipationObligation.DIRECT_REQUIRED,
            reason_codes=("invalid",),
        )


def test_silence_decision_rejects_contribution():
    with pytest.raises(ValueError, match="silence"):
        ParticipationDecision(
            action=ParticipationAction.SILENCE,
            scene=InteractionScene.AMBIENT_CONTRIBUTION,
            act=None,
            posture=ResponsePosture.POLITE,
            obligation=ParticipationObligation.NONE,
            reason_codes=("invalid",),
            contribution="不该存在",
        )
