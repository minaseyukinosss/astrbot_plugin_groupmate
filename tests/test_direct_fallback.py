import asyncio

from groupmate.core.response_act import ResponseAct
from groupmate.engine.direct_fallback import DirectFallbackComposer
from groupmate.engine.rate_limit import BudgetTracker, SlidingWindowRateLimiter
from groupmate.models import GroupPolicy, TopicSnapshot, TriggerKind
from groupmate.social.affinity import ResponsePosture
from tests.fakes import StaticGenerationModel
from tests.test_workflow import build_workflow


def test_fallback_text_is_deterministic_for_act_and_affinity_posture():
    composer = DirectFallbackComposer()

    assert composer.compose(ResponseAct.ACKNOWLEDGE, ResponsePosture.POLITE) == "在呢。"
    assert (
        composer.compose(ResponseAct.PLAYFUL_REPLY, ResponsePosture.WARM)
        == "好啦，叫这么多次做什么呀。"
    )
    assert (
        composer.compose(ResponseAct.BOUNDARY, ResponsePosture.FIRM)
        == "别一直空叫我。"
    )


def _direct_topic(message_factory):
    message = message_factory(message_id="direct-fallback", text="小爱")
    return TopicSnapshot("fallback-topic", "g1", (message,), 100, 100)


def test_direct_generation_error_uses_minimal_fallback(
    message_factory,
    balanced_policy,
):
    class BrokenGeneration(StaticGenerationModel):
        async def generate(self, plan, topic, memories):
            raise RuntimeError("provider unavailable")

    platform = None
    workflow = build_workflow(generator=BrokenGeneration("unused"), platform=platform)

    outcome = asyncio.run(
        workflow.evaluate(
            _direct_topic(message_factory),
            TriggerKind.ALIAS_DIRECT,
            balanced_policy,
        )
    )

    assert outcome.sent is True
    assert outcome.text == "在呢。"


def test_direct_generation_budget_exhaustion_skips_model_and_uses_fallback(
    message_factory,
    balanced_policy,
):
    model = StaticGenerationModel("不应调用")
    limiter = SlidingWindowRateLimiter(hourly_limit=6, cooldown_seconds=0)
    budgets = BudgetTracker(limiter, generation_hourly_limit=1)
    budgets.record_generation(101)
    workflow = build_workflow(generator=model, budgets=budgets)

    outcome = asyncio.run(
        workflow.evaluate(
            _direct_topic(message_factory),
            TriggerKind.ALIAS_DIRECT,
            balanced_policy,
        )
    )

    assert outcome.sent is True
    assert outcome.text == "在呢。"
    assert model.calls == 0


def test_direct_model_silence_uses_fallback(message_factory, balanced_policy):
    workflow = build_workflow(generator=StaticGenerationModel("<SILENCE>"))

    outcome = asyncio.run(
        workflow.evaluate(
            _direct_topic(message_factory),
            TriggerKind.ALIAS_DIRECT,
            balanced_policy,
        )
    )

    assert outcome.sent is True
    assert outcome.text == "在呢。"
