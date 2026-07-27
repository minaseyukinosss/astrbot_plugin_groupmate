"""Phase 4 budgets：generation / send / cost。"""

from __future__ import annotations

import asyncio

from groupmate.engine.rate_limit import BudgetTracker, SlidingWindowRateLimiter
from groupmate.engine.workflow import CognitiveWorkflow
from groupmate.models import ChatMessage, GroupPolicy, TopicSnapshot, TriggerKind
from groupmate.persona.aemeath import AemeathOutputFirewall, AemeathPersonaProvider
from tests.fakes import (
    FakeClock,
    FakeMemoryRepository,
    FakePlatform,
    NullVision,
    StaticGenerationModel,
)


def test_budget_tracker_send_and_generation_independent():
    send = SlidingWindowRateLimiter(hourly_limit=2, cooldown_seconds=0)
    budgets = BudgetTracker(send, generation_hourly_limit=3, cost_hourly_limit=2)
    now = 1000
    assert budgets.allow_send(now)
    assert budgets.allow_generation(now)
    budgets.record_generation(now)
    budgets.record_generation(now + 1)
    budgets.record_generation(now + 2)
    assert budgets.allow_generation(now + 3) is False
    assert budgets.allow_send(now + 3) is True
    budgets.record_send(now + 3)
    budgets.record_cost(now + 3)
    budgets.record_cost(now + 4)
    assert budgets.allow_cost(now + 5) is False


def test_arbiter_reject_does_not_consume_send():
    model = StaticGenerationModel("在呢。")
    limiter = SlidingWindowRateLimiter(hourly_limit=6, cooldown_seconds=0)
    budgets = BudgetTracker(limiter)
    workflow = CognitiveWorkflow(
        generation_model=model,
        vision=NullVision(),
        platform=FakePlatform(),
        memory=FakeMemoryRepository(),
        persona=AemeathPersonaProvider(),
        output_guard=AemeathOutputFirewall(max_chars=60),
        rate_limiter=limiter,
        clock=FakeClock(200),
        budgets=budgets,
    )
    topic = TopicSnapshot(
        topic_id="t1",
        group_id="g1",
        messages=(
            ChatMessage(
                message_id="b1",
                group_id="g1",
                sender_id="__bot__",
                sender_name="爱弥斯",
                text="嗯",
                timestamp=98,
                is_bot=True,
            ),
            ChatMessage(
                message_id="b2",
                group_id="g1",
                sender_id="__bot__",
                sender_name="爱弥斯",
                text="好",
                timestamp=99,
                is_bot=True,
            ),
            ChatMessage(
                message_id="m1",
                group_id="g1",
                sender_id="u1",
                sender_name="Alice",
                text="爱弥斯 路过",
                timestamp=100,
            ),
        ),
        created_at=98,
        updated_at=100,
    )
    outcome = asyncio.run(
        workflow.evaluate(
            topic,
            TriggerKind.ALIAS_MENTION,
            GroupPolicy(humanize_delay_enabled=False, spontaneous_cooldown_seconds=0),
        )
    )
    assert outcome.sent is False
    assert model.calls == 0
    assert budgets.generation_count(200) == 0
    assert limiter.snapshot(200) == ()


def test_successful_candidate_consumes_send_and_generation():
    model = StaticGenerationModel("可以这样。")
    limiter = SlidingWindowRateLimiter(hourly_limit=6, cooldown_seconds=0)
    budgets = BudgetTracker(limiter)
    workflow = CognitiveWorkflow(
        generation_model=model,
        vision=NullVision(),
        platform=FakePlatform(),
        memory=FakeMemoryRepository(),
        persona=AemeathPersonaProvider(),
        output_guard=AemeathOutputFirewall(max_chars=180),
        rate_limiter=limiter,
        clock=FakeClock(105),
        budgets=budgets,
    )
    topic = TopicSnapshot(
        topic_id="t1",
        group_id="g1",
        messages=(
            ChatMessage(
                message_id="m1",
                group_id="g1",
                sender_id="u1",
                sender_name="Alice",
                text="爱弥斯 这个怎么弄？",
                timestamp=100,
            ),
        ),
        created_at=100,
        updated_at=100,
    )
    outcome = asyncio.run(
        workflow.evaluate(
            topic,
            TriggerKind.CANDIDATE,
            GroupPolicy(humanize_delay_enabled=False, spontaneous_cooldown_seconds=0),
        )
    )
    assert outcome.sent is True
    assert model.calls == 1
    assert budgets.generation_count(105) == 1
    assert len(limiter.snapshot(105)) == 1
