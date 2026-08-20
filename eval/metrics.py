"""Deterministic, label-driven metrics for Social Runtime evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .schema import EvaluationLabel


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


@dataclass(frozen=True)
class ConfusionMatrix:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    def add(self, *, actual: bool, predicted: bool) -> "ConfusionMatrix":
        return ConfusionMatrix(
            tp=self.tp + int(actual and predicted),
            fp=self.fp + int(not actual and predicted),
            fn=self.fn + int(actual and not predicted),
        )

    def to_dict(self) -> dict[str, float | int]:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "precision": _ratio(self.tp, self.tp + self.fp),
            "recall": _ratio(self.tp, self.tp + self.fn),
        }


@dataclass(frozen=True)
class MetricSummary:
    attention: ConfusionMatrix
    action: ConfusionMatrix
    target: ConfusionMatrix
    open_participation: ConfusionMatrix
    miss_rate: float
    interrupt_rate: float
    monopoly_rate: float
    repetition_rate: float
    target_concentration: float
    autonomy: Mapping[str, float | int]
    quality: Mapping[str, float]

    def to_dict(self) -> dict[str, object]:
        return {
            "attention": self.attention.to_dict(),
            "action": self.action.to_dict(),
            "target": self.target.to_dict(),
            "open_participation": self.open_participation.to_dict(),
            "miss_rate": self.miss_rate,
            "interrupt_rate": self.interrupt_rate,
            "monopoly_rate": self.monopoly_rate,
            "repetition_rate": self.repetition_rate,
            "target_concentration": self.target_concentration,
            "autonomy": dict(self.autonomy),
            "quality": dict(self.quality),
        }


def _prediction(record: Mapping[str, object]) -> Mapping[str, object]:
    value = record.get("prediction", {})
    if not isinstance(value, Mapping):
        raise ValueError("evaluation prediction must be a mapping")
    return value


def _label(record: Mapping[str, object]) -> EvaluationLabel:
    value = record.get("label")
    if not isinstance(value, Mapping):
        raise ValueError("evaluation label must be a mapping")
    return EvaluationLabel.from_dict(value)


def collect_metrics(records: Iterable[Mapping[str, object]]) -> MetricSummary:
    """Score structured predictions against immutable human fixture labels.

    This function deliberately consumes a prediction, never a model explanation.
    Optional quality booleans are emitted by structured runtime contracts and are
    averaged only when that particular aspect was evaluated.
    """

    attention = ConfusionMatrix()
    action = ConfusionMatrix()
    target = ConfusionMatrix()
    open_participation = ConfusionMatrix()
    actual_actions = 0
    missed_actions = 0
    predicted_actions = 0
    interrupts = 0
    action_texts: list[str] = []
    predicted_targets: list[str] = []
    autonomous_values: list[float] = []
    autonomous_expiry: list[bool] = []
    quality_values: dict[str, list[bool]] = {
        "persona": [],
        "relationship": [],
        "culture": [],
        "task": [],
        "delivery": [],
        "recovery": [],
        "style": [],
        "media": [],
    }

    for record in records:
        truth = _label(record)
        predicted = _prediction(record)
        predicted_attention = bool(predicted.get("attention"))
        predicted_action = bool(predicted.get("action"))
        predicted_target = str(predicted.get("target") or "").strip() or None

        attention = attention.add(actual=truth.attention, predicted=predicted_attention)
        action = action.add(actual=truth.action, predicted=predicted_action)
        if truth.target is not None and predicted_target is not None:
            if predicted_target == truth.target:
                target = target.add(actual=True, predicted=True)
            else:
                target = ConfusionMatrix(target.tp, target.fp + 1, target.fn + 1)
        else:
            target = target.add(
                actual=truth.target is not None,
                predicted=predicted_target is not None,
            )

        actual_open = truth.action and truth.target is None
        predicted_open = bool(predicted.get("open_participation"))
        open_participation = open_participation.add(
            actual=actual_open, predicted=predicted_open
        )

        if truth.action:
            actual_actions += 1
            missed_actions += int(not predicted_action)
        if predicted_action:
            predicted_actions += 1
            interrupts += int(not truth.action)
            if predicted_target is not None:
                predicted_targets.append(predicted_target)
            text = str(predicted.get("text") or "").strip()
            if text:
                action_texts.append(text)

        if bool(predicted.get("autonomous")):
            autonomous_values.append(float(predicted.get("autonomy_value", 0.0)))
            autonomous_expiry.append(not bool(predicted.get("expired")))

        for metric, field in (
            ("persona", "persona_ok"),
            ("relationship", "relationship_ok"),
            ("culture", "culture_ok"),
            ("task", "task_ok"),
            ("delivery", "delivery_ok"),
            ("recovery", "recovery_ok"),
            ("style", "style_ok"),
            ("media", "media_ok"),
        ):
            if field in predicted:
                quality_values[metric].append(bool(predicted[field]))

    target_counts = {value: predicted_targets.count(value) for value in set(predicted_targets)}
    repeated = len(action_texts) - len(set(action_texts))
    quality = {
        name: _ratio(sum(values), len(values)) if values else 0.0
        for name, values in quality_values.items()
    }
    autonomy = {
        "count": len(autonomous_values),
        "mean_value": _ratio(sum(autonomous_values), len(autonomous_values)),
        "expiry_correct": _ratio(sum(autonomous_expiry), len(autonomous_expiry)),
    }
    concentration = _ratio(max(target_counts.values()) if target_counts else 0, len(predicted_targets))
    return MetricSummary(
        attention=attention,
        action=action,
        target=target,
        open_participation=open_participation,
        miss_rate=_ratio(missed_actions, actual_actions),
        interrupt_rate=_ratio(interrupts, predicted_actions),
        monopoly_rate=concentration,
        repetition_rate=_ratio(repeated, len(action_texts)),
        target_concentration=concentration,
        autonomy=autonomy,
        quality=quality,
    )


__all__ = ("ConfusionMatrix", "MetricSummary", "collect_metrics")
