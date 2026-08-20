"""Deterministic, label-driven metrics for Social Runtime evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable, Mapping

from .schema import EvaluationLabel


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


@dataclass(frozen=True)
class ConfusionMatrix:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    def add(self, *, actual: bool, predicted: bool) -> "ConfusionMatrix":
        return ConfusionMatrix(
            tp=self.tp + int(actual and predicted),
            fp=self.fp + int(not actual and predicted),
            fn=self.fn + int(actual and not predicted),
            tn=self.tn + int(not actual and not predicted),
        )

    def to_dict(self) -> dict[str, float | int]:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "support": self.tp + self.fp + self.fn + self.tn,
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
    quality: Mapping[str, float | None]

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
    for name in ("attention", "action"):
        if type(value.get(name)) is not bool:
            raise ValueError(f"prediction {name} must be a boolean")
    if value.get("target") is not None and not isinstance(value["target"], str):
        raise ValueError("prediction target must be a string or null")
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
    autonomous_count = 0
    monopoly_groupmate = 0
    monopoly_total = 0
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
                target = ConfusionMatrix(target.tp, target.fp + 1, target.fn + 1, target.tn)
        else:
            target = target.add(
                actual=truth.target is not None,
                predicted=predicted_target is not None,
            )

        actual_open = truth.action and truth.target is None
        predicted_open = predicted_action and predicted_target is None
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
            autonomous_count += 1
            frozen_truth = record.get("frozen_truth", {})
            if not isinstance(frozen_truth, Mapping):
                raise ValueError("frozen_truth must be a mapping")
            value = frozen_truth.get("autonomy_value")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                numeric = float(value)
                if not isfinite(numeric):
                    raise ValueError("frozen autonomy_value must be finite")
                autonomous_values.append(numeric)
            decision_offset = predicted.get("decision_offset_ms")
            if type(decision_offset) is int:
                autonomous_expiry.append(decision_offset <= truth.expires_after_ms)

        conversation = record.get("conversation", {})
        if isinstance(conversation, Mapping):
            groupmate_count = conversation.get("groupmate_action_count")
            member_count = conversation.get("member_action_count")
            if type(groupmate_count) is int and type(member_count) is int:
                monopoly_groupmate += groupmate_count
                monopoly_total += groupmate_count + member_count

        frozen_truth = record.get("frozen_truth", {})
        if not isinstance(frozen_truth, Mapping):
            raise ValueError("frozen_truth must be a mapping")
        for metric in quality_values:
            if type(frozen_truth.get(metric)) is bool:
                quality_values[metric].append(frozen_truth[metric])

    target_counts = {value: predicted_targets.count(value) for value in set(predicted_targets)}
    repeated = len(action_texts) - len(set(action_texts))
    quality = {
        name: _ratio(sum(values), len(values)) if values else None
        for name, values in quality_values.items()
    }
    autonomy = {
        "count": autonomous_count,
        "mean_value": _ratio(sum(autonomous_values), len(autonomous_values)) if autonomous_values else None,
        "expiry_correct": _ratio(sum(autonomous_expiry), len(autonomous_expiry)) if autonomous_expiry else None,
    }
    concentration = _ratio(max(target_counts.values()) if target_counts else 0, len(predicted_targets))
    return MetricSummary(
        attention=attention,
        action=action,
        target=target,
        open_participation=open_participation,
        miss_rate=_ratio(missed_actions, actual_actions),
        interrupt_rate=_ratio(interrupts, predicted_actions),
        monopoly_rate=_ratio(monopoly_groupmate, monopoly_total),
        repetition_rate=_ratio(repeated, len(action_texts)),
        target_concentration=concentration,
        autonomy=autonomy,
        quality=quality,
    )


__all__ = ("ConfusionMatrix", "MetricSummary", "collect_metrics")
