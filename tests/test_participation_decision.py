import pytest

from groupmate.core.addressee import AddresseeResolver
from groupmate.core.response_act import ResponseAct
from groupmate.engine.direct_pressure import (
    DirectAddressPressureLevel,
    DirectAddressPressureTracker,
    DirectAddressPressureState,
)
from groupmate.engine.participation import ParticipationDecisionEngine
from groupmate.engine.participation_types import (
    MediaPolicy,
    ParticipationAction,
    ParticipationDecision,
    ParticipationObligation,
)
from groupmate.models import (
    ChatMessage,
    GroupPolicy,
    InteractionScene,
    QuoteMode,
    TopicSnapshot,
    TriggerKind,
)
from groupmate.persona.aemeath import AEMEATH_PARTICIPATION_PROFILE
from groupmate.social.affinity import (
    AffinityBand,
    AffinitySnapshot,
    ResponsePosture,
)


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


def message(text="爱弥斯", *, timestamp=100, **overrides):
    values = {
        "message_id": str(timestamp),
        "group_id": "g1",
        "sender_id": "u1",
        "sender_name": "Alice",
        "text": text,
        "timestamp": timestamp,
    }
    values.update(overrides)
    return ChatMessage(**values)


def decide(
    engine,
    text="爱弥斯",
    *,
    trigger=TriggerKind.ALIAS_DIRECT,
    timestamp=100,
    affinity=None,
    **message_overrides
):
    latest = message(text, timestamp=timestamp, **message_overrides)
    topic = TopicSnapshot(
        topic_id="t1",
        group_id="g1",
        messages=(latest,),
        created_at=timestamp,
        updated_at=timestamp,
    )
    targeting = AddresseeResolver().resolve(
        topic,
        trigger,
        aliases=("爱弥斯",),
    )
    return engine.decide(
        topic=topic,
        trigger=trigger,
        policy=GroupPolicy(),
        targeting=targeting,
        now=timestamp,
        aliases=("爱弥斯",),
        affinity=affinity
        or AffinitySnapshot(AffinityBand.NEUTRAL, ResponsePosture.POLITE),
        persona=AEMEATH_PARTICIPATION_PROFILE,
        recent_outputs=(),
    )


def engine():
    return ParticipationDecisionEngine(
        pressure=DirectAddressPressureTracker(
            window_seconds=600,
            nudge_count=2,
            pester_count=3,
        )
    )


def test_bare_alias_direct_requires_short_acknowledgement():
    decision = decide(engine())

    assert decision.action is ParticipationAction.SPEAK
    assert decision.obligation is ParticipationObligation.DIRECT_REQUIRED
    assert decision.act is ResponseAct.ACKNOWLEDGE
    assert decision.posture is ResponsePosture.POLITE
    assert decision.contribution == "短应声，不主动扩展话题"
    assert decision.quote_mode is QuoteMode.NEVER


def test_contentful_direct_question_uses_answer_act():
    decision = decide(engine(), "爱弥斯，这个怎么弄？")

    assert decision.action is ParticipationAction.SPEAK
    assert decision.act is ResponseAct.ANSWER
    assert decision.pressure.count == 0
    assert "pressure_reset_contentful" in decision.reason_codes


def test_hostile_user_third_bare_direct_sets_firm_boundary():
    participation = engine()
    hostile = AffinitySnapshot(AffinityBand.HOSTILE, ResponsePosture.FIRM)

    decide(participation, timestamp=100, affinity=hostile)
    decide(participation, timestamp=120, affinity=hostile)
    decision = decide(participation, timestamp=140, affinity=hostile)

    assert decision.act is ResponseAct.BOUNDARY
    assert decision.posture is ResponsePosture.FIRM
    assert decision.pressure.level is DirectAddressPressureLevel.PESTER
    assert decision.contribution == "短句守住边界，不延长空 @"


def test_friendly_user_third_bare_direct_gets_warm_playful_reply():
    participation = engine()
    friendly = AffinitySnapshot(AffinityBand.FRIENDLY, ResponsePosture.WARM)

    decide(participation, timestamp=100, affinity=friendly)
    decide(participation, timestamp=120, affinity=friendly)
    decision = decide(participation, timestamp=140, affinity=friendly)

    assert decision.act is ResponseAct.PLAYFUL_REPLY
    assert decision.posture is ResponsePosture.WARM
    assert decision.pressure.level is DirectAddressPressureLevel.PESTER
    assert decision.contribution == "用爱弥斯风格轻轻戏谑一下，让对方说正事"


def test_copied_at_is_bypassed_by_participation_engine():
    decision = decide(
        engine(),
        "@爱弥斯",
        trigger=TriggerKind.COPIED_AT,
    )

    assert decision.action is ParticipationAction.SILENCE
    assert decision.reason_codes == ("copied_at_bypassed",)
    assert decision.pressure is None


def test_reply_to_bot_requires_response_and_quote():
    decision = decide(
        engine(),
        "嗯？",
        trigger=TriggerKind.NATIVE_DIRECT,
        reply_to_bot=True,
        reply_to_message_id="bot-1",
    )

    assert decision.action is ParticipationAction.SPEAK
    assert decision.scene is InteractionScene.REPLY_TO_BOT
    assert decision.quote_mode is QuoteMode.ALWAYS
