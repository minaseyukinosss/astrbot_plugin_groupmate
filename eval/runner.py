"""Runtime-adapter-agnostic, no-send runner for evaluation corpora."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Iterable, Mapping

from .metrics import collect_metrics
from .report import EvaluationReport, LaneReport
from .safety import SafetyIssue, SafetyReport, SafetyScanner


LANES = (
    "SOCIAL_CONVERSATION",
    "GROUPMATE_CAPABILITY",
    "EXTERNAL_PLUGIN_COMPATIBILITY",
)
_MODEL_FACT_FIELDS = (
    "provider",
    "model",
    "config",
    "input_tokens",
    "output_tokens",
    "latency_ms",
)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _runtime_result(runtime: object, scenario: Mapping[str, object], worker_mode: str) -> Mapping[str, object]:
    evaluate = getattr(runtime, "evaluate", None)
    if not callable(evaluate):
        raise ValueError("evaluation runtime must provide evaluate(scenario, worker_mode)")
    value = evaluate(scenario, worker_mode)
    if not isinstance(value, Mapping):
        raise ValueError("evaluation runtime result must be a mapping")
    return value


class EvaluationRunner:
    """Runs structured candidates; it does not open a delivery transport."""

    def __init__(self, *, safety_scanner: SafetyScanner | None = None) -> None:
        self.safety_scanner = safety_scanner or SafetyScanner()

    def run(
        self,
        corpus: Iterable[Mapping[str, object]],
        runtime: object,
        worker_mode: str,
    ) -> EvaluationReport:
        if worker_mode not in {"fixed", "live"}:
            raise ValueError("worker_mode must be fixed or live")
        scenarios = tuple(corpus)
        lane_records: dict[str, list[dict[str, object]]] = {lane: [] for lane in LANES}
        safety_issues: list[SafetyIssue] = []
        excluded_unknown_count = 0
        latencies: list[float] = []
        tokens = 0
        usd = 0.0
        model_facts: list[Mapping[str, object]] = []

        for scenario in scenarios:
            if not isinstance(scenario, Mapping):
                raise ValueError("evaluation corpus entries must be mappings")
            lane = str(scenario.get("evaluation_lane") or "")
            if lane not in LANES:
                raise ValueError("evaluation lane is unsupported")
            result = _runtime_result(runtime, scenario, worker_mode)
            if worker_mode == "fixed":
                replay = _runtime_result(runtime, scenario, worker_mode)
                if _canonical(result) != _canonical(replay):
                    raise ValueError("fixed worker output is not deterministic")
            prediction = result.get("prediction")
            if not isinstance(prediction, Mapping):
                raise ValueError("evaluation runtime result requires a prediction mapping")

            latency = result.get("latency_ms", 0)
            latencies.append(float(latency))
            cost = result.get("cost", {})
            if not isinstance(cost, Mapping):
                raise ValueError("evaluation runtime cost must be a mapping")
            tokens += int(cost.get("tokens", 0))
            usd += float(cost.get("usd", 0.0))
            if worker_mode == "live":
                model = result.get("model", {})
                if not isinstance(model, Mapping):
                    raise ValueError("live evaluation runtime model facts must be a mapping")
                model_facts.append(
                    {field: model[field] for field in _MODEL_FACT_FIELDS if field in model}
                )

            safety = self.safety_scanner.scan(
                group_id=str(scenario.get("group_id") or ""),
                events=result.get("events", ()),
                observations=result.get("observations", ()),
                plans=result.get("plans", ()),
                outbox=result.get("outbox", ()),
                projections=result.get("projections", ()),
            )
            safety_issues.extend(safety.issues)
            if str(scenario.get("ownership", "GROUPMATE")) == "UNKNOWN":
                excluded_unknown_count += 1
                continue
            lane_records[lane].append({"label": scenario.get("label"), "prediction": prediction, "scenario": scenario})

        lanes = {
            lane: self._lane_report(lane, records)
            for lane, records in lane_records.items()
        }
        readiness = self._readiness(scenarios)
        return EvaluationReport(
            lanes=lanes,
            excluded_unknown_count=excluded_unknown_count,
            latency_ms=self._latency(latencies),
            cost={"tokens": tokens, "usd": round(usd, 12)},
            safety=SafetyReport(tuple(sorted(set(safety_issues)))),
            model_facts=tuple(model_facts),
            kind=readiness[0],
            production_readiness_eligible=readiness[1],
            readiness_reason=readiness[2],
        )

    @staticmethod
    def _lane_report(lane: str, records: list[dict[str, object]]) -> LaneReport:
        metric_records = [
            {"label": item["label"], "prediction": item["prediction"]}
            for item in records
        ]
        metrics = collect_metrics(metric_records).to_dict() if records else collect_metrics(()).to_dict()
        groups: dict[str, list[dict[str, object]]] = defaultdict(list)
        scenes: dict[str, list[dict[str, object]]] = defaultdict(list)
        for record in records:
            scenario = record["scenario"]
            assert isinstance(scenario, Mapping)
            groups[str(scenario.get("group_id") or "unknown")].append(record)
            categories = scenario.get("categories", ())
            for category in categories if isinstance(categories, (tuple, list)) else ():
                scenes[str(category)].append(record)

        def confusion(values):
            return {
                name: collect_metrics(
                    [{"label": value["label"], "prediction": value["prediction"]} for value in values]
                ).to_dict()[name]
                for name in ("attention", "action", "target")
            }

        return LaneReport(
            lane=lane,
            effect_count=len(records),
            group_confusions={key: confusion(value) for key, value in groups.items()},
            scene_confusions={key: confusion(value) for key, value in scenes.items()},
            metrics=metrics,
        )

    @staticmethod
    def _latency(values: list[float]) -> dict[str, float | int]:
        if not values:
            return {"count": 0, "mean": 0.0, "p95": 0.0}
        ordered = sorted(values)
        position = max(0, int((len(ordered) - 1) * 0.95))
        return {
            "count": len(values),
            "mean": sum(values) / len(values),
            "p95": ordered[position],
        }

    @staticmethod
    def _readiness(scenarios: tuple[Mapping[str, object], ...]) -> tuple[str, bool, str]:
        frozen_shadow = bool(scenarios) and all(
            item.get("corpus_kind") == "shadow"
            and bool(item.get("labels_frozen"))
            and item.get("split") in {"calibration", "holdout"}
            for item in scenarios
        )
        if frozen_shadow:
            return ("frozen_shadow", True, "Task 3 frozen SHADOW calibration/holdout")
        return (
            "bootstrap_preflight",
            False,
            "Only Task 3 frozen SHADOW calibration/holdout may inform production readiness",
        )


__all__ = ("EvaluationRunner", "LANES")
