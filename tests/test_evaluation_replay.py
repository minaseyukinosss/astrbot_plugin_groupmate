import asyncio

import pytest

from groupmate.evaluation.evaluator import DecisionEvaluator
from groupmate.evaluation.replay import OfflineReplayRunner, VirtualClock
from groupmate.evaluation.models import EvaluationCase, EvaluationLabel, ExpectedOutcome
from groupmate.models import ChatMessage, Decision, GroupPolicy, TriggerKind
from tests.fakes import FailingDecisionModel, StaticDecisionModel


def make_case(text="普通消息", expected=EvaluationLabel.MUST_SILENCE, **overrides):
    values = {
        "message_id": "m1",
        "group_id": "eval-group",
        "sender_id": "u1",
        "sender_name": "群友甲",
        "text": text,
        "timestamp": 1000,
    }
    values.update(overrides)
    return EvaluationCase(
        schema_version=1,
        case_id="case-1",
        description="测试场景",
        messages=(ChatMessage(**values),),
        expected=ExpectedOutcome(expected),
        tags=("ordinary",),
    )


def run(coroutine):
    return asyncio.run(coroutine)


def test_alias_direct_does_not_call_decision_model():
    model = StaticDecisionModel(Decision.ignore("unused"))
    result = run(
        DecisionEvaluator(model, GroupPolicy()).evaluate(
            make_case("小爱，在吗", EvaluationLabel.MUST_RESPOND)
        )
    )
    assert result.action == "respond"
    assert result.trigger is TriggerKind.ALIAS_DIRECT
    assert result.reason_code == "alias_direct"
    assert result.decision_model_called is False
    assert model.calls == 0


def test_ordinary_message_uses_decision_model():
    model = StaticDecisionModel(
        Decision.respond("补充一句", confidence=0.9, reason_code="useful_contribution")
    )
    result = run(
        DecisionEvaluator(model, GroupPolicy()).evaluate(
            make_case(expected=EvaluationLabel.MUST_RESPOND)
        )
    )
    assert result.action == "respond"
    assert result.decision_model_called is True
    assert result.matched is True


def test_low_confidence_defaults_to_silence():
    model = StaticDecisionModel(Decision.respond("补充", confidence=0.2))
    result = run(
        DecisionEvaluator(model, GroupPolicy(decision_threshold=0.7)).evaluate(
            make_case()
        )
    )
    assert result.action == "ignore"
    assert result.reason_code == "below_threshold"


def test_model_error_defaults_to_silence():
    result = run(
        DecisionEvaluator(FailingDecisionModel(), GroupPolicy()).evaluate(make_case())
    )
    assert result.action == "ignore"
    assert result.error_code == "decision_error"


@pytest.mark.parametrize(
    ("message_overrides", "trigger", "action"),
    [
        ({"is_command": True}, TriggerKind.COMMAND, "ignore"),
        ({"mentions_bot": True}, TriggerKind.NATIVE_DIRECT, "bypass"),
        ({"is_bot": True}, TriggerKind.IGNORE, "ignore"),
    ],
)
def test_deterministic_routes_do_not_call_model(message_overrides, trigger, action):
    model = StaticDecisionModel(Decision.respond("unused"))
    result = run(
        DecisionEvaluator(model, GroupPolicy()).evaluate(
            make_case(**message_overrides)
        )
    )
    assert result.trigger is trigger
    assert result.action == action
    assert model.calls == 0


def test_offline_runner_uses_virtual_time_without_sleep(monkeypatch):
    async def forbidden_sleep(delay):
        raise AssertionError("离线回放不能调用真实 sleep")

    monkeypatch.setattr(asyncio, "sleep", forbidden_sleep)
    first = make_case()
    second = EvaluationCase(
        schema_version=1,
        case_id="case-2",
        description="第二个场景",
        messages=(
            ChatMessage("m2", "eval-group", "u2", "群友乙", "普通消息", 2000),
        ),
        expected=ExpectedOutcome(EvaluationLabel.MUST_SILENCE),
    )
    clock = VirtualClock()
    runner = OfflineReplayRunner(
        DecisionEvaluator(StaticDecisionModel(Decision.ignore("safe")), GroupPolicy()),
        clock,
    )
    predictions = run(runner.run((first, second)))
    assert len(predictions) == 2
    assert clock.now() == 2000
    assert all(item.latency_ms == 0 for item in predictions)


def test_offline_prediction_is_reproducible():
    case = make_case()
    evaluator = DecisionEvaluator(
        StaticDecisionModel(Decision.ignore("safe")), GroupPolicy()
    )
    first = run(evaluator.evaluate(case))
    second = run(evaluator.evaluate(case))
    assert first == second
