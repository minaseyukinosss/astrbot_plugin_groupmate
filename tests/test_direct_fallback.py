import asyncio

from groupmate.core.response_act import ResponseAct
from groupmate.engine.direct_fallback import DirectFallbackComposer
from groupmate.engine.rate_limit import BudgetTracker, SlidingWindowRateLimiter
from groupmate.models import TopicSnapshot, TriggerKind
from groupmate.persona.aemeath.output_firewall import AemeathOutputFirewall
from groupmate.social.affinity import ResponsePosture
from tests.fakes import StaticGenerationModel
from tests.test_workflow import build_workflow


def test_fallback_text_is_deterministic_for_act_and_affinity_posture():
    composer = DirectFallbackComposer()

    assert composer.compose(ResponseAct.ACKNOWLEDGE, ResponsePosture.POLITE) == "在呢"
    assert (
        composer.compose(ResponseAct.PLAYFUL_REPLY, ResponsePosture.WARM)
        == "好啦一直叫我干嘛呀"
    )
    assert composer.compose(ResponseAct.BOUNDARY, ResponsePosture.FIRM) == "别一直叫"
    assert composer.compose(ResponseAct.RECIPROCATE, ResponsePosture.WARM) == "谢谢你啦"
    assert (
        composer.compose(ResponseAct.TASK_UNSUPPORTED, ResponsePosture.WARM)
        == "这个我真搞不定呀"
    )
    assert composer.compose(ResponseAct.ANSWER, ResponsePosture.WARM) == "脑子卡住了呀"
    assert composer.compose(ResponseAct.VISUAL_REACTION, ResponsePosture.CLOSE) == "收到图啦"


def test_all_fallback_lines_pass_aemeath_firewall_and_stay_short():
    composer = DirectFallbackComposer()
    firewall = AemeathOutputFirewall()
    reason_sets = (
        (),
        ("poke_direct",),
        ("poke_spam",),
        ("poke_bystander",),
    )

    for reasons in reason_sets:
        for act in ResponseAct:
            for posture in ResponsePosture:
                text = composer.compose(act, posture, reason_codes=reasons)
                result = firewall.validate(text, [], response_act=act)
                assert result.accepted, (reasons, act, posture, text, result.codes)
                assert 1 <= len(text) <= 30, (act, posture, text)
                assert "。" not in text and "？" not in text and "！" not in text
                # 避免状态汇报/客服残留
                for banned in ("处理中", "心意收到", "信息不够", "关键信息", "做不了"):
                    assert banned not in text, (act, posture, text, banned)


def test_poke_spam_fallback_uses_poke_specific_copy():
    composer = DirectFallbackComposer()

    assert (
        composer.compose(
            ResponseAct.PLAYFUL_REPLY,
            ResponsePosture.WARM,
            reason_codes=("poke_direct", "poke_spam"),
        )
        == "好啦别戳啦"
    )
    assert (
        composer.compose(
            ResponseAct.PLAYFUL_REPLY,
            ResponsePosture.CLOSE,
            reason_codes=("poke_direct",),
        )
        == "再戳我可回手啦"
    )
    assert (
        composer.compose(
            ResponseAct.BOUNDARY,
            ResponsePosture.FIRM,
            reason_codes=("poke_spam",),
        )
        == "别一直戳"
    )


def test_poke_bystander_fallback_uses_scene_copy():
    composer = DirectFallbackComposer()

    assert (
        composer.compose(
            ResponseAct.PLAYFUL_REPLY,
            ResponsePosture.POLITE,
            reason_codes=("poke_bystander",),
        )
        == "这也太闲了吧"
    )
    assert (
        composer.compose(
            ResponseAct.PLAYFUL_REPLY,
            ResponsePosture.CLOSE,
            reason_codes=("poke_bystander",),
        )
        == "那我戳一下"
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
    assert outcome.text == "在呢"


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
    assert outcome.text == "在呢"
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
    assert outcome.text == "在呢"
