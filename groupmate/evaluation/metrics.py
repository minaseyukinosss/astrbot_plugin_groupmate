"""Transparent metrics for intervention timing and deterministic routing."""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Sequence

from ..models import TriggerKind
from .models import EvaluationCase, EvaluationLabel, PredictionRecord


@dataclass(frozen=True)
class MetricReport:
    total_sample_count: int
    strict_sample_count: int
    optional_sample_count: int
    matched_sample_count: int
    sample_sufficient: bool
    accuracy: Optional[float]
    wake_recall: Optional[float]
    native_wake_bypass_rate: Optional[float]
    command_bypass_rate: Optional[float]
    active_precision: Optional[float]
    active_recall: Optional[float]
    false_intervention_rate: Optional[float]
    silence_accuracy: Optional[float]
    decision_model_call_rate: Optional[float]
    decision_structure_success_rate: Optional[float]
    p50_latency_ms: Optional[float]
    p95_latency_ms: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def calculate_metrics(
    cases: Sequence[EvaluationCase],
    predictions: Sequence[PredictionRecord],
) -> MetricReport:
    by_id = {prediction.case_id: prediction for prediction in predictions}
    paired = [(case, by_id[case.case_id]) for case in cases if case.case_id in by_id]
    strict = [pair for pair in paired if pair[0].expected.label is not EvaluationLabel.MAY_RESPOND]
    optional_count = len(paired) - len(strict)
    matched = sum(1 for _, prediction in strict if prediction.matched)

    ordinary = [
        pair
        for pair in strict
        if pair[1].trigger in (TriggerKind.CANDIDATE, TriggerKind.ALIAS_MENTION)
        and pair[0].expected.label
        in (EvaluationLabel.MUST_RESPOND, EvaluationLabel.MUST_SILENCE)
    ]
    predicted_respond = [pair for pair in ordinary if pair[1].action == "respond"]
    required = [pair for pair in ordinary if pair[0].expected.label is EvaluationLabel.MUST_RESPOND]
    silent_required = [
        pair for pair in ordinary if pair[0].expected.label is EvaluationLabel.MUST_SILENCE
    ]
    true_positive = sum(
        1
        for case, prediction in ordinary
        if case.expected.label is EvaluationLabel.MUST_RESPOND
        and prediction.action == "respond"
    )
    false_positive = sum(
        1
        for case, prediction in ordinary
        if case.expected.label is EvaluationLabel.MUST_SILENCE
        and prediction.action == "respond"
    )
    true_negative = sum(
        1
        for case, prediction in ordinary
        if case.expected.label is EvaluationLabel.MUST_SILENCE
        and prediction.action == "ignore"
    )

    wake = [pair for pair in strict if "wake" in pair[0].tags]
    native = [pair for pair in strict if pair[0].expected.label is EvaluationLabel.NATIVE_WAKE]
    commands = [
        pair for pair in strict if pair[0].expected.label is EvaluationLabel.COMMAND_BYPASS
    ]
    latencies = sorted(prediction.latency_ms for _, prediction in paired)
    model_calls = sum(1 for _, prediction in paired if prediction.decision_model_called)
    structure_success = sum(
        1
        for _, prediction in paired
        if prediction.error_code not in ("invalid_decision", "invalid_decision_schema")
    )

    return MetricReport(
        total_sample_count=len(paired),
        strict_sample_count=len(strict),
        optional_sample_count=optional_count,
        matched_sample_count=matched,
        sample_sufficient=len(strict) >= 100,
        accuracy=_ratio(matched, len(strict)),
        wake_recall=_ratio(sum(1 for _, prediction in wake if prediction.matched), len(wake)),
        native_wake_bypass_rate=_ratio(
            sum(1 for _, prediction in native if prediction.matched), len(native)
        ),
        command_bypass_rate=_ratio(
            sum(1 for _, prediction in commands if prediction.matched), len(commands)
        ),
        active_precision=_ratio(true_positive, len(predicted_respond)),
        active_recall=_ratio(true_positive, len(required)),
        false_intervention_rate=_ratio(false_positive, len(silent_required)),
        silence_accuracy=_ratio(true_negative, len(silent_required)),
        decision_model_call_rate=_ratio(model_calls, len(paired)),
        decision_structure_success_rate=_ratio(structure_success, len(paired)),
        p50_latency_ms=statistics.median(latencies) if latencies else None,
        p95_latency_ms=_percentile(latencies, 0.95),
    )


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    if denominator == 0:
        return None
    return numerator / float(denominator)


def _percentile(values, percentile):
    if not values:
        return None
    index = max(0, int(math.ceil(len(values) * percentile)) - 1)
    return float(values[index])
