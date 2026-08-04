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
    InteractionScene,
    MessageOrigin,
    QuoteMode,
    TopicSnapshot,
    TriggerKind,
)
from groupmate.persona.aemeath import AEMEATH_PARTICIPATION_PROFILE
from groupmate.policies import BehaviorPolicy, InteractionPolicy
from groupmate.engine.poke_throttle import PokeThrottle
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
    interaction=None,
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
        persona_id="aemeath",
        topic=topic,
        trigger=trigger,
        policy=BehaviorPolicy().participation,
        targeting=targeting,
        now=timestamp,
        aliases=("爱弥斯",),
        affinity=affinity
        or AffinitySnapshot(AffinityBand.NEUTRAL, ResponsePosture.POLITE),
        persona=AEMEATH_PARTICIPATION_PROFILE,
        recent_outputs=(),
        interaction=interaction
        or InteractionPolicy(
            poke_react_probability=1.0,
            poke_bystander_probability=1.0,
            poke_cooldown_seconds=0,
            poke_bystander_cooldown_seconds=0,
        ),
    )


def engine():
    return ParticipationDecisionEngine(
        pressure=DirectAddressPressureTracker(
            window_seconds=600,
            nudge_count=2,
            pester_count=3,
        ),
        poke_throttle=PokeThrottle(rng=lambda: 0.0),
    )


def decide_poke(
    engine,
    *,
    timestamp=100,
    affinity=None,
    interaction=None,
    poke_role="direct",
    target_id="bot",
):
    return decide(
        engine,
        "",
        trigger=TriggerKind.HOST_INTERACTION,
        timestamp=timestamp,
        affinity=affinity,
        interaction=interaction,
        segment_types=("poke",),
        origin=MessageOrigin.SYSTEM_SYNTHETIC,
        metadata={
            "interaction_kind": "poke",
            "poke_role": poke_role,
            "target_id": target_id,
            "poker_id": "u1",
            "source_adapter": "aiocqhttp_poke",
        },
    )


def test_bare_alias_direct_requires_short_acknowledgement():
    decision = decide(engine())

    assert decision.action is ParticipationAction.SPEAK
    assert decision.obligation is ParticipationObligation.DIRECT_REQUIRED
    assert decision.act is ResponseAct.ACKNOWLEDGE
    assert decision.posture is ResponsePosture.POLITE
    assert decision.contribution == "短应声，不主动扩展话题"
    assert decision.quote_mode is QuoteMode.NEVER


def test_first_neutral_poke_requires_playful_direct_response():
    decision = decide_poke(engine())

    assert decision.action is ParticipationAction.SPEAK
    assert decision.obligation is ParticipationObligation.DIRECT_REQUIRED
    assert decision.scene is InteractionScene.DIRECT_INTERACTION
    assert decision.act is ResponseAct.PLAYFUL_REPLY
    assert decision.quote_mode is QuoteMode.NEVER
    assert decision.pressure.count == 1
    assert "对方戳的是你" in decision.contribution
    assert "用「你」对说话者" in decision.contribution


def test_friendly_repeated_poke_stays_playful():
    participation = engine()
    friendly = AffinitySnapshot(AffinityBand.FRIENDLY, ResponsePosture.WARM)

    decide_poke(participation, timestamp=100, affinity=friendly)
    decide_poke(participation, timestamp=120, affinity=friendly)
    decision = decide_poke(participation, timestamp=140, affinity=friendly)

    assert decision.act is ResponseAct.PLAYFUL_REPLY
    assert decision.posture is ResponsePosture.WARM
    assert decision.pressure.level is DirectAddressPressureLevel.PESTER


def test_hostile_repeated_poke_sets_firm_boundary():
    participation = engine()
    hostile = AffinitySnapshot(AffinityBand.HOSTILE, ResponsePosture.FIRM)

    decide_poke(participation, timestamp=100, affinity=hostile)
    decide_poke(participation, timestamp=120, affinity=hostile)
    decision = decide_poke(participation, timestamp=140, affinity=hostile)

    assert decision.act is ResponseAct.BOUNDARY
    assert decision.posture is ResponsePosture.FIRM
    assert decision.pressure.level is DirectAddressPressureLevel.PESTER
    assert "poke_spam" in decision.reason_codes


def test_poke_cooldown_silences_second_reaction():
    participation = engine()
    interaction = InteractionPolicy(
        poke_react_probability=1.0,
        poke_cooldown_seconds=30,
    )

    first = decide_poke(participation, timestamp=100, interaction=interaction)
    second = decide_poke(participation, timestamp=110, interaction=interaction)

    assert first.action is ParticipationAction.SPEAK
    assert second.action is ParticipationAction.SILENCE
    assert "poke_cooldown" in second.reason_codes


def test_bystander_poke_can_speak_or_skip_by_probability():
    speak = decide_poke(
        engine(),
        poke_role="bystander",
        target_id="u2",
        interaction=InteractionPolicy(poke_bystander_probability=1.0),
    )
    skip = decide_poke(
        engine(),
        poke_role="bystander",
        target_id="u2",
        interaction=InteractionPolicy(poke_bystander_probability=0.0),
    )

    assert speak.action is ParticipationAction.SPEAK
    assert "poke_bystander" in speak.reason_codes
    assert speak.obligation is ParticipationObligation.OPEN_OPTIONAL
    assert skip.action is ParticipationAction.SILENCE
    assert "poke_bystander_skip" in skip.reason_codes


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


def test_ambiguous_multi_user_target_overrides_direct_trigger():
    decision = decide(
        engine(),
        "@Bob @Carol 你们看",
        trigger=TriggerKind.ALIAS_DIRECT,
        mentioned_user_ids=("u2", "u3"),
    )

    assert decision.action is ParticipationAction.SILENCE
    assert decision.reason_codes == ("inhibit:ambiguous_target",)


def decide_topic(
    engine,
    messages,
    *,
    trigger=TriggerKind.CANDIDATE,
    affinity=None,
):
    topic = TopicSnapshot(
        topic_id="t-open",
        group_id="g1",
        messages=tuple(messages),
        created_at=messages[0].timestamp,
        updated_at=messages[-1].timestamp,
    )
    targeting = AddresseeResolver().resolve(
        topic,
        trigger,
        aliases=("爱弥斯",),
    )
    return engine.decide(
        persona_id="aemeath",
        topic=topic,
        trigger=trigger,
        policy=BehaviorPolicy().participation,
        targeting=targeting,
        now=messages[-1].timestamp,
        aliases=("爱弥斯",),
        affinity=affinity
        or AffinitySnapshot(AffinityBand.NEUTRAL, ResponsePosture.POLITE),
        persona=AEMEATH_PARTICIPATION_PROFILE,
        recent_outputs=(),
    )


def test_open_group_concrete_help_request_can_speak():
    decision = decide(
        engine(),
        "有没有人知道这个插件怎么重载？",
        trigger=TriggerKind.CANDIDATE,
    )

    assert decision.action is ParticipationAction.SPEAK
    assert decision.obligation is ParticipationObligation.OPEN_OPTIONAL
    assert decision.act is ResponseAct.ANSWER
    assert decision.reason_codes == ("motive:help_when_concrete",)


def test_empty_echo_candidate_silences():
    decision = decide(
        engine(),
        "哈哈哈",
        trigger=TriggerKind.CANDIDATE,
    )

    assert decision.action is ParticipationAction.SILENCE
    assert decision.reason_codes == ("inhibit:empty_echo",)


def test_passing_alias_mention_does_not_become_open_help():
    decision = decide(
        engine(),
        "爱弥斯好像也不知道怎么弄吧？",
        trigger=TriggerKind.ALIAS_MENTION,
    )

    assert decision.action is ParticipationAction.SILENCE
    assert decision.reason_codes == ("inhibit:passing_alias_mention",)


def test_reply_to_other_user_silences_even_when_it_contains_help_words():
    previous = message(
        "你会配置吗？",
        timestamp=100,
        sender_id="u2",
    )
    latest = message(
        "这个要怎么弄？",
        timestamp=110,
        reply_to_message_id=previous.message_id,
        reply_to_bot=False,
    )

    decision = decide_topic(engine(), (previous, latest))

    assert decision.action is ParticipationAction.SILENCE
    assert decision.reason_codes == ("inhibit:owned_by_other_user",)


def test_recent_bot_density_suppresses_open_participation():
    messages = (
        message(
            "先看配置。",
            timestamp=100,
            sender_id="bot",
            is_bot=True,
        ),
        message(
            "再重载。",
            timestamp=101,
            sender_id="bot",
            is_bot=True,
        ),
        message(
            "有没有人知道这个插件怎么重载？",
            timestamp=102,
        ),
    )

    decision = decide_topic(engine(), messages)

    assert decision.action is ParticipationAction.SILENCE
    assert decision.reason_codes == ("inhibit:avoid_monopoly",)
