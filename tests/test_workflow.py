import asyncio

from groupmate.guardrails import AemeathOutputGuard
from groupmate.models import Decision, TriggerKind
from groupmate.rate_limit import SlidingWindowRateLimiter
from groupmate.workflow import CognitiveWorkflow
from tests.fakes import (
    FailingDecisionModel,
    FakeClock,
    FakeMemoryRepository,
    FakePlatform,
    NullVision,
    StaticDecisionModel,
    StaticGenerationModel,
    StaticPersona,
)


def build_workflow(decider, generator=None, platform=None, memory=None):
    return CognitiveWorkflow(
        decision_model=decider,
        generation_model=generator or StaticGenerationModel("这也太离谱了呀。"),
        vision=NullVision(),
        platform=platform or FakePlatform(),
        memory=memory or FakeMemoryRepository(),
        persona=StaticPersona(),
        output_guard=AemeathOutputGuard(max_chars=60),
        rate_limiter=SlidingWindowRateLimiter(hourly_limit=6, cooldown_seconds=0),
        clock=FakeClock(),
    )


def test_model_failure_fails_closed(topic_snapshot, balanced_policy):
    platform = FakePlatform()
    workflow = build_workflow(FailingDecisionModel(), platform=platform)

    outcome = asyncio.run(
        workflow.evaluate(topic_snapshot, TriggerKind.CANDIDATE, balanced_policy)
    )

    assert outcome.sent is False
    assert outcome.reason == "decision_error"
    assert platform.sent == []


def test_valid_decision_generates_guards_and_sends(topic_snapshot, balanced_policy):
    platform = FakePlatform()
    memory = FakeMemoryRepository()
    workflow = build_workflow(
        StaticDecisionModel(Decision.respond("给一句自然反应", confidence=0.9)),
        generator=StaticGenerationModel("这也太离谱了呀。"),
        platform=platform,
        memory=memory,
    )

    outcome = asyncio.run(
        workflow.evaluate(topic_snapshot, TriggerKind.CANDIDATE, balanced_policy)
    )

    assert outcome.sent is True
    assert platform.sent[0]["text"] == "这也太离谱了呀。"
    assert memory.outbox[outcome.decision_id]["sent_at"] == 101


def test_alias_direct_bypasses_decision_model(topic_snapshot, balanced_policy):
    decider = StaticDecisionModel(Decision.ignore("should_not_run"))
    workflow = build_workflow(decider)

    outcome = asyncio.run(
        workflow.evaluate(topic_snapshot, TriggerKind.ALIAS_DIRECT, balanced_policy)
    )

    assert outcome.sent is True
    assert decider.calls == 0


def test_low_confidence_decision_stays_silent(topic_snapshot, balanced_policy):
    workflow = build_workflow(
        StaticDecisionModel(Decision.respond("也许回复", confidence=0.2))
    )

    outcome = asyncio.run(
        workflow.evaluate(topic_snapshot, TriggerKind.CANDIDATE, balanced_policy)
    )

    assert outcome.sent is False
    assert outcome.reason == "below_threshold"

