"""Runtime-adapter-agnostic, no-send runner for evaluation corpora."""

from __future__ import annotations

import json
import math
import hashlib
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
_CANDIDATE_QUALITY_FIELDS = frozenset({"task", "delivery", "recovery"})


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def frozen_artifact_digest(
    scenarios: Iterable[Mapping[str, object]],
) -> str:
    normalized = [
        {
            key: value
            for key, value in scenario.items()
            if key != "shadow_provenance"
        }
        for scenario in scenarios
    ]
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


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
        scenarios = tuple(corpus)
        if worker_mode == "fixed":
            first = self._run_once(scenarios, runtime, worker_mode)
            second = self._run_once(scenarios, runtime, worker_mode)
            if first.to_json() != second.to_json():
                raise ValueError("fixed worker report is not deterministic")
            return first
        return self._run_once(scenarios, runtime, worker_mode)

    def _run_once(
        self,
        corpus: tuple[Mapping[str, object], ...],
        runtime: object,
        worker_mode: str,
    ) -> EvaluationReport:
        if worker_mode not in {"fixed", "live"}:
            raise ValueError("worker_mode must be fixed or live")
        scenarios = corpus
        lane_records: dict[str, list[dict[str, object]]] = {lane: [] for lane in LANES}
        safety_issues: list[SafetyIssue] = []
        excluded_unknown_count = 0
        latencies: list[float] = []
        tokens = 0
        usd = 0.0
        model_facts: list[Mapping[str, object]] = []
        candidates: list[Mapping[str, object]] = []

        for scenario in scenarios:
            if not isinstance(scenario, Mapping):
                raise ValueError("evaluation corpus entries must be mappings")
            lane = str(scenario.get("evaluation_lane") or "")
            if lane not in LANES:
                raise ValueError("evaluation lane is unsupported")
            result = _runtime_result(runtime, scenario, worker_mode)
            prediction = result.get("prediction")
            if not isinstance(prediction, Mapping):
                raise ValueError("evaluation runtime result requires a prediction mapping")
            candidate_quality = self._candidate_quality(result.get("quality"))
            candidates.append({
                "scenario_id": scenario.get("scenario_id"),
                "prediction": prediction,
                "quality": candidate_quality,
            })

            latency = result.get("latency_ms")
            if (
                isinstance(latency, bool)
                or not isinstance(latency, (int, float))
                or not math.isfinite(float(latency))
                or latency < 0
            ):
                raise ValueError("runtime latency_ms must be finite and non-negative")
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
                required = {"provider", "model", "config", "input_tokens", "output_tokens", "latency_ms"}
                if set(model) & required != required:
                    raise ValueError("live model facts are incomplete")
                if not isinstance(model["provider"], str) or not model["provider"].strip() or not isinstance(model["model"], str) or not model["model"].strip() or not isinstance(model["config"], Mapping):
                    raise ValueError("live model facts are invalid")
                try:
                    json.dumps(model["config"], allow_nan=False, sort_keys=True)
                except (TypeError, ValueError):
                    raise ValueError("live model facts are invalid")
                for name in ("input_tokens", "output_tokens", "latency_ms"):
                    value = model[name]
                    if type(value) is not int or value < 0:
                        raise ValueError("live model facts are invalid")
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
            ownership = scenario.get("ownership")
            if ownership not in {"GROUPMATE", "EXTERNAL_PLUGIN", "UNKNOWN"}:
                ownership = "UNKNOWN"
            candidate_owner = result.get("candidate_owner")
            if candidate_owner not in {"GROUPMATE", "EXTERNAL_PLUGIN", "UNKNOWN"}:
                candidate_owner = "UNKNOWN"
            producer = scenario.get("candidate_producer")
            if producer != "GROUPMATE":
                producer = "UNKNOWN"
            if ownership == "UNKNOWN" or producer == "UNKNOWN" or candidate_owner != "GROUPMATE":
                excluded_unknown_count += 1
                continue
            provenance = scenario.get("context_provenance", {})
            complete_context = (
                isinstance(provenance, Mapping)
                and type(provenance.get("complete_member_context")) is bool
                and provenance["complete_member_context"]
            )
            lane_records[lane].append({
                "label": scenario.get("label"),
                "prediction": prediction,
                "candidate_quality": candidate_quality,
                "scenario": scenario,
                "social_applicable": complete_context,
                "result": result,
            })

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
            candidate_digest=hashlib.sha256(_canonical(candidates).encode()).hexdigest(),
        )

    @staticmethod
    def _lane_report(lane: str, records: list[dict[str, object]]) -> LaneReport:
        metric_records = [
            {
                "label": item["label"],
                "prediction": item["prediction"],
                "frozen_truth": item["candidate_quality"],
            }
            for item in records
            if item["social_applicable"]
        ]
        metrics = collect_metrics(metric_records).to_dict() if metric_records else collect_metrics(()).to_dict()
        if not metric_records:
            for name in ("attention", "action", "target", "open_participation"):
                metrics[name] = None
        metrics["style_applicable"] = any(
            isinstance(item["scenario"].get("context_provenance"), Mapping)
            and bool(item["scenario"]["context_provenance"].get("bot_only"))
            for item in records
        )
        groups: dict[str, list[dict[str, object]]] = defaultdict(list)
        scenes: dict[str, list[dict[str, object]]] = defaultdict(list)
        for record in records:
            scenario = record["scenario"]
            assert isinstance(scenario, Mapping)
            if record["social_applicable"]:
                groups[str(scenario.get("group_id") or "unknown")].append(record)
            categories = scenario.get("categories", ())
            if record["social_applicable"]:
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
            compatibility=(
                EvaluationRunner._compatibility(records)
                if lane == "EXTERNAL_PLUGIN_COMPATIBILITY"
                else None
            ),
        )

    @staticmethod
    def _candidate_quality(value: object) -> dict[str, bool]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("candidate quality must be a mapping")
        unknown = set(value) - _CANDIDATE_QUALITY_FIELDS
        if unknown:
            raise ValueError("candidate quality contains an unrecognized field")
        if any(type(item) is not bool for item in value.values()):
            raise ValueError("candidate quality values must be booleans")
        return {str(name): bool(item) for name, item in value.items()}

    @staticmethod
    def _compatibility(records):
        no_steal = no_duplicate = no_self_attribution = 0
        for item in records:
            scenario = item["scenario"]
            result = item["result"]
            correlation = scenario.get("external_response_correlation")
            delivered = EvaluationRunner._has_groupmate_delivery(result, correlation)
            silent = not bool(item["prediction"].get("action")) and not delivered
            no_steal += int(silent)
            no_duplicate += int(not delivered)
            no_self_attribution += int(
                scenario.get("external_response_owner") == "EXTERNAL_PLUGIN" and silent
            )
        return {
            "no_steal": no_steal,
            "no_duplicate": no_duplicate,
            "no_self_attribution": no_self_attribution,
        }

    @staticmethod
    def _has_groupmate_delivery(result, correlation):
        def walk(value, inherited_correlation=None):
            if isinstance(value, Mapping):
                current_correlation = value.get("correlation_id", inherited_correlation)
                if "part" in value or "platform_message_id" in value or "response" in value or "idempotency_key" in value:
                    if correlation is None or current_correlation == correlation:
                        return True
                return any(walk(child, current_correlation) for child in value.values())
            if isinstance(value, (tuple, list)):
                return any(walk(child, inherited_correlation) for child in value)
            return False
        return walk(result.get("outbox", ())) or walk(result.get("responses", ()))

    @staticmethod
    def _latency(values: list[float]) -> dict[str, float | int]:
        if not values:
            return {"count": 0, "mean": 0.0, "p95": 0.0}
        ordered = sorted(values)
        position = max(0, math.ceil(len(ordered) * 0.95) - 1)
        return {
            "count": len(values),
            "mean": sum(values) / len(values),
            "p95": ordered[position],
        }

    @staticmethod
    def _readiness(scenarios: tuple[Mapping[str, object], ...]) -> tuple[str, bool, str]:
        splits = {item.get("split") for item in scenarios}
        scenario_digest = hashlib.sha256(json.dumps(
            [
                {"scenario_id": item.get("scenario_id"), "split": item.get("split"), "evaluation_lane": item.get("evaluation_lane")}
                for item in scenarios
            ], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()).hexdigest()
        label_digest = hashlib.sha256(json.dumps(
            [{"scenario_id": item.get("scenario_id"), "label": item.get("label")} for item in scenarios],
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()).hexdigest()
        artifact_digest = frozen_artifact_digest(scenarios)
        frozen_shadow = bool(scenarios) and splits == {"calibration", "holdout"} and all(
            item.get("corpus_kind") == "shadow"
            and type(item.get("labels_frozen")) is bool and item["labels_frozen"]
            and isinstance(item.get("shadow_provenance"), Mapping)
            and item["shadow_provenance"].get("kind") == "installed_live_shadow"
            and type(item["shadow_provenance"].get("manifest_version")) is int
            and item["shadow_provenance"].get("installed") is True
            and item["shadow_provenance"].get("runtime_mode") == "SHADOW"
            and item["shadow_provenance"].get("frozen") is True
            and item["shadow_provenance"].get("scenario_digest") == scenario_digest
            and item["shadow_provenance"].get("label_digest") == label_digest
            and item["shadow_provenance"].get("artifact_digest") == artifact_digest
            for item in scenarios
        )
        if frozen_shadow:
            return ("frozen_shadow", True, "Task 3 frozen SHADOW calibration/holdout")
        return (
            "bootstrap_preflight",
            False,
            "Only Task 3 frozen SHADOW calibration/holdout may inform production readiness",
        )


__all__ = ("EvaluationRunner", "LANES", "frozen_artifact_digest")
