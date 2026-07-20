from groupmate.evaluation.metrics import calculate_metrics
from groupmate.evaluation.models import (
    EvaluationCase,
    EvaluationLabel,
    ExpectedOutcome,
    PredictionRecord,
)
from groupmate.models import ChatMessage, TriggerKind


def case(case_id, label, tags=()):
    return EvaluationCase(
        schema_version=1,
        case_id=case_id,
        description=case_id,
        messages=(ChatMessage(case_id, "g", "u", "甲", "消息", 100),),
        expected=ExpectedOutcome(label),
        tags=tags,
    )


def prediction(case_id, label, action, trigger=TriggerKind.CANDIDATE, matched=True):
    return PredictionRecord(
        case_id=case_id,
        expected_label=label,
        trigger=trigger,
        action=action,
        confidence=0.9,
        reason_code="test",
        target_message_id=None,
        decision_model_called=trigger is TriggerKind.CANDIDATE,
        latency_ms=10.0,
        error_code=None,
        matched=matched,
    )


def test_optional_cases_are_excluded_from_strict_metrics():
    cases = (
        case("r", EvaluationLabel.MUST_RESPOND),
        case("s", EvaluationLabel.MUST_SILENCE),
        case("o", EvaluationLabel.MAY_RESPOND),
    )
    predictions = (
        prediction("r", EvaluationLabel.MUST_RESPOND, "respond"),
        prediction("s", EvaluationLabel.MUST_SILENCE, "ignore"),
        prediction("o", EvaluationLabel.MAY_RESPOND, "respond"),
    )
    report = calculate_metrics(cases, predictions)
    assert report.strict_sample_count == 2
    assert report.optional_sample_count == 1
    assert report.accuracy == 1.0
    assert report.sample_sufficient is False


def test_active_precision_and_recall_use_strict_labels():
    cases = (
        case("tp", EvaluationLabel.MUST_RESPOND),
        case("fn", EvaluationLabel.MUST_RESPOND),
        case("fp", EvaluationLabel.MUST_SILENCE),
        case("tn", EvaluationLabel.MUST_SILENCE),
    )
    predictions = (
        prediction("tp", EvaluationLabel.MUST_RESPOND, "respond"),
        prediction("fn", EvaluationLabel.MUST_RESPOND, "ignore", matched=False),
        prediction("fp", EvaluationLabel.MUST_SILENCE, "respond", matched=False),
        prediction("tn", EvaluationLabel.MUST_SILENCE, "ignore"),
    )
    report = calculate_metrics(cases, predictions)
    assert report.active_precision == 0.5
    assert report.active_recall == 0.5
    assert report.false_intervention_rate == 0.5
    assert report.silence_accuracy == 0.5


def test_zero_denominator_returns_none():
    cases = (case("s", EvaluationLabel.MUST_SILENCE),)
    predictions = (prediction("s", EvaluationLabel.MUST_SILENCE, "ignore"),)
    report = calculate_metrics(cases, predictions)
    assert report.active_precision is None
    assert report.active_recall is None


def test_latency_percentiles_are_reported():
    cases = tuple(case(str(index), EvaluationLabel.MUST_SILENCE) for index in range(4))
    predictions = tuple(
        PredictionRecord(
            **dict(
                prediction(str(index), EvaluationLabel.MUST_SILENCE, "ignore").__dict__,
                latency_ms=float(value),
            )
        )
        for index, value in enumerate((1, 2, 3, 100))
    )
    report = calculate_metrics(cases, predictions)
    assert report.p50_latency_ms == 2.5
    assert report.p95_latency_ms == 100.0
