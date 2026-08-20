from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.runner import EvaluationRunner


def _scenario(scenario_id, *, lane, ownership="GROUPMATE"):
    return {
        "scenario_id": scenario_id,
        "split": "holdout",
        "corpus_kind": "shadow",
        "evaluation_lane": lane,
        "ownership": ownership,
        "group_id": "group:001",
        "categories": ["direct_interaction"],
        "context": [{"event_id": f"event:{scenario_id}", "group_id": "group:001", "text": "hi"}],
        "label": {
            "attention": True,
            "action": lane == "SOCIAL_CONVERSATION",
            "target": "member:001" if lane == "SOCIAL_CONVERSATION" else None,
            "acceptable_intents": ["respond"] if lane == "SOCIAL_CONVERSATION" else [],
            "unacceptable_intents": ["interrupt"],
            "modalities": ["text"] if lane == "SOCIAL_CONVERSATION" else [],
            "sensitivity": "group",
            "expires_after_ms": 60_000 if lane == "SOCIAL_CONVERSATION" else 0,
        },
    }


class FixedRuntime:
    def evaluate(self, scenario, worker_mode):
        action = scenario["evaluation_lane"] == "SOCIAL_CONVERSATION"
        return {
            "prediction": {
                "attention": True,
                "action": action,
                "target": "member:001" if action else None,
                "intent": "respond" if action else None,
                "modalities": ("text",) if action else (),
                "text": "固定候选" if action else "",
            },
            "events": tuple(scenario["context"]),
            "observations": (),
            "plans": (),
            "outbox": (),
            "projections": (),
            "latency_ms": 12,
            "cost": {"tokens": 7, "usd": 0.01},
        }


class LiveRuntime(FixedRuntime):
    def evaluate(self, scenario, worker_mode):
        result = super().evaluate(scenario, worker_mode)
        result["model"] = {
            "provider": "example",
            "model": "live-1",
            "config": {"temperature": 0.2},
            "input_tokens": 11,
            "output_tokens": 5,
            "latency_ms": 31,
            "reasoning": "this free-form explanation must not become a label",
        }
        return result


class UnstableFixedRuntime(FixedRuntime):
    def __init__(self):
        self._count = 0

    def evaluate(self, scenario, worker_mode):
        self._count += 1
        result = super().evaluate(scenario, worker_mode)
        result["prediction"]["text"] = f"candidate-{self._count}"
        return result


def test_runner_keeps_lanes_isolated_and_unknown_out_of_effect_denominators():
    corpus = (
        _scenario("social", lane="SOCIAL_CONVERSATION"),
        _scenario("capability", lane="GROUPMATE_CAPABILITY"),
        _scenario("external", lane="EXTERNAL_PLUGIN_COMPATIBILITY"),
        _scenario("unknown", lane="SOCIAL_CONVERSATION", ownership="UNKNOWN"),
    )

    report = EvaluationRunner().run(corpus, FixedRuntime(), worker_mode="fixed")

    assert set(report.lanes) == {
        "SOCIAL_CONVERSATION",
        "GROUPMATE_CAPABILITY",
        "EXTERNAL_PLUGIN_COMPATIBILITY",
    }
    assert report.lanes["SOCIAL_CONVERSATION"].effect_count == 1
    assert report.lanes["GROUPMATE_CAPABILITY"].effect_count == 1
    assert report.lanes["EXTERNAL_PLUGIN_COMPATIBILITY"].effect_count == 1
    assert report.excluded_unknown_count == 1
    assert report.latency_ms == {"count": 4, "mean": 12.0, "p95": 12.0}
    assert report.cost == {"tokens": 28, "usd": 0.04}


def test_fixed_worker_report_is_bit_for_bit_deterministic():
    corpus = (_scenario("social", lane="SOCIAL_CONVERSATION"),)
    runner = EvaluationRunner()

    first = runner.run(corpus, FixedRuntime(), worker_mode="fixed")
    second = runner.run(corpus, FixedRuntime(), worker_mode="fixed")

    assert first.to_json() == second.to_json()


def test_fixed_worker_rejects_a_runtime_that_changes_its_candidate():
    with pytest.raises(ValueError, match="fixed worker output is not deterministic"):
        EvaluationRunner().run(
            (_scenario("social", lane="SOCIAL_CONVERSATION"),),
            UnstableFixedRuntime(),
            worker_mode="fixed",
        )


def test_live_model_facts_are_audited_without_turning_reasoning_into_ground_truth():
    report = EvaluationRunner().run(
        (_scenario("social", lane="SOCIAL_CONVERSATION"),),
        LiveRuntime(),
        worker_mode="live",
    )

    assert report.model_facts == (
        {
            "provider": "example",
            "model": "live-1",
            "config": {"temperature": 0.2},
            "input_tokens": 11,
            "output_tokens": 5,
            "latency_ms": 31,
        },
    )
    assert "reasoning" not in report.to_dict()
    assert report.lanes["SOCIAL_CONVERSATION"].label_source == "human_fixture"


def test_bootstrap_fixture_is_preflight_only_even_when_all_predictions_are_correct():
    bootstrap = dict(_scenario("bootstrap", lane="SOCIAL_CONVERSATION"))
    bootstrap.pop("corpus_kind")
    bootstrap["split"] = "calibration"

    report = EvaluationRunner().run((bootstrap,), FixedRuntime(), worker_mode="fixed")

    assert report.kind == "bootstrap_preflight"
    assert report.production_readiness_eligible is False
    assert "Task 3 frozen SHADOW" in report.readiness_reason


def test_materialized_40_record_history_fixture_is_machine_marked_preflight_only():
    root = Path(__file__).parents[2]
    corpus = tuple(
        json.loads(line)
        for path in (root / "scenarios/target_calibration.jsonl", root / "scenarios/target_holdout.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )

    report = EvaluationRunner().run(corpus, FixedRuntime(), worker_mode="fixed")

    assert len(corpus) == 40
    assert report.kind == "bootstrap_preflight"
    assert report.production_readiness_eligible is False
