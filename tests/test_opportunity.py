"""OpportunityArbiter：硬触发、软应说/应沉默。"""

from __future__ import annotations

import asyncio
from inspect import signature

from groupmate.core.addressee import AddresseeResolver
from groupmate.engine.opportunity import OpportunityArbiter, UTILITY_THRESHOLD
from groupmate.engine.rate_limit import BudgetTracker, SlidingWindowRateLimiter
from groupmate.engine.workflow import CognitiveWorkflow
from groupmate.models import (
    ChatMessage,
    GroupPolicy,
    OpportunityAction,
    TopicSnapshot,
    TriggerKind,
)
from groupmate.persona.aemeath import AemeathOutputFirewall, AemeathPersonaProvider
from tests.fakes import (
    FakeClock,
    FakeMemoryRepository,
    FakePlatform,
    NullVision,
    StaticGenerationModel,
)


def _msg(**overrides):
    values = {
        "message_id": "m1",
        "group_id": "g1",
        "sender_id": "u1",
        "sender_name": "Alice",
        "text": "你好",
        "timestamp": 100,
    }
    values.update(overrides)
    return ChatMessage(**values)


def _topic(*messages):
    return TopicSnapshot(
        topic_id="t1",
        group_id="g1",
        messages=tuple(messages),
        created_at=messages[0].timestamp,
        updated_at=messages[-1].timestamp,
    )


def _policy(**kwargs):
    values = {"humanize_delay_enabled": False, "spontaneous_cooldown_seconds": 0}
    values.update(kwargs)
    return GroupPolicy(**values)


def test_hard_trigger_always_speaks_without_utility():
    arbiter = OpportunityArbiter(
        budgets=BudgetTracker(SlidingWindowRateLimiter(hourly_limit=6, cooldown_seconds=0))
    )
    topic = _topic(_msg(text="路过"))
    targeting = AddresseeResolver().resolve(topic, TriggerKind.ALIAS_DIRECT)
    opp = arbiter.evaluate(
        topic, TriggerKind.ALIAS_DIRECT, _policy(), targeting, now=200
    )
    assert opp.action is OpportunityAction.SPEAK
    assert "hard_trigger" in opp.reason_codes
    assert opp.confidence == 1.0


def test_opportunity_arbiter_has_no_continuous_affinity_input():
    parameters = signature(OpportunityArbiter.evaluate).parameters

    assert "favorability" not in parameters


def test_opportunity_reasons_have_no_relationship_score_bonus():
    arbiter = OpportunityArbiter(
        budgets=BudgetTracker(
            SlidingWindowRateLimiter(hourly_limit=6, cooldown_seconds=0)
        )
    )
    topic = _topic(_msg(text="爱弥斯 这个怎么弄？"))
    targeting = AddresseeResolver().resolve(topic, TriggerKind.ALIAS_MENTION)

    opportunity = arbiter.evaluate(
        topic, TriggerKind.ALIAS_MENTION, _policy(), targeting, now=100
    )

    assert not any(reason.startswith("rel=") for reason in opportunity.reason_codes)


def test_soft_passing_mention_tends_to_silence():
    arbiter = OpportunityArbiter(
        threshold=UTILITY_THRESHOLD,
        budgets=BudgetTracker(
            SlidingWindowRateLimiter(hourly_limit=6, cooldown_seconds=0)
        ),
    )
    # 高 bot 密度 + 无问句 → 应沉默
    topic = _topic(
        _msg(message_id="b1", sender_id="__bot__", sender_name="爱弥斯", text="嗯", is_bot=True, timestamp=98),
        _msg(message_id="b2", sender_id="__bot__", sender_name="爱弥斯", text="好", is_bot=True, timestamp=99),
        _msg(message_id="m1", text="爱弥斯 好像也在", timestamp=100),
    )
    targeting = AddresseeResolver().resolve(topic, TriggerKind.ALIAS_MENTION)
    opp = arbiter.evaluate(
        topic,
        TriggerKind.ALIAS_MENTION,
        _policy(),
        targeting,
        now=100,
        recent_outputs=("嗯", "好"),
    )
    assert opp.action is OpportunityAction.SILENCE


def test_soft_help_question_should_speak():
    arbiter = OpportunityArbiter(
        budgets=BudgetTracker(
            SlidingWindowRateLimiter(hourly_limit=6, cooldown_seconds=0)
        )
    )
    topic = _topic(_msg(text="爱弥斯 这个怎么弄？"))
    targeting = AddresseeResolver().resolve(topic, TriggerKind.ALIAS_MENTION)
    opp = arbiter.evaluate(
        topic, TriggerKind.ALIAS_MENTION, _policy(), targeting, now=100
    )
    assert opp.action is OpportunityAction.SPEAK


def test_workflow_soft_silence_skips_generation():
    model = StaticGenerationModel("不该生成")
    memory = FakeMemoryRepository()
    workflow = CognitiveWorkflow(
        generation_model=model,
        vision=NullVision(),
        platform=FakePlatform(),
        memory=memory,
        persona=AemeathPersonaProvider(),
        output_guard=AemeathOutputFirewall(max_chars=60),
        rate_limiter=SlidingWindowRateLimiter(hourly_limit=6, cooldown_seconds=0),
        clock=FakeClock(200),
    )
    topic = _topic(
        _msg(message_id="b1", is_bot=True, sender_id="__bot__", text="嗨", timestamp=98),
        _msg(message_id="b2", is_bot=True, sender_id="__bot__", text="呀", timestamp=99),
        _msg(text="爱弥斯 路过", timestamp=100),
    )
    outcome = asyncio.run(
        workflow.evaluate(topic, TriggerKind.ALIAS_MENTION, _policy())
    )
    assert outcome.sent is False
    assert model.calls == 0


def test_flag_off_restores_legacy_soft_generation():
    model = StaticGenerationModel("<SILENCE>")
    workflow = CognitiveWorkflow(
        generation_model=model,
        vision=NullVision(),
        platform=FakePlatform(),
        memory=FakeMemoryRepository(),
        persona=AemeathPersonaProvider(),
        output_guard=AemeathOutputFirewall(max_chars=60),
        rate_limiter=SlidingWindowRateLimiter(hourly_limit=6, cooldown_seconds=0),
        clock=FakeClock(105),
    )
    topic = _topic(_msg(text="爱弥斯 路过一下", timestamp=100))
    outcome = asyncio.run(
        workflow.evaluate(
            topic,
            TriggerKind.ALIAS_MENTION,
            _policy(v3_opportunity_enabled=False),
        )
    )
    assert model.calls == 1
    assert outcome.sent is False
    assert outcome.reason == "model_silence"
