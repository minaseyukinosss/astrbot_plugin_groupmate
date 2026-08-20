from __future__ import annotations

import json
import hashlib
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
        "candidate_producer": "GROUPMATE",
        "context_provenance": {"complete_member_context": True},
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
            "candidate_owner": "GROUPMATE",
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


class IncompleteLiveRuntime(FixedRuntime):
    def evaluate(self, scenario, worker_mode):
        result = super().evaluate(scenario, worker_mode)
        result["model"] = {"provider": "example", "model": "missing-facts"}
        return result


class InvalidLiveRuntime(FixedRuntime):
    def evaluate(self, scenario, worker_mode):
        result = super().evaluate(scenario, worker_mode)
        result["model"] = {
            "provider": "example", "model": "bad", "config": {"temperature": float("nan")},
            "input_tokens": 1, "output_tokens": 1, "latency_ms": 1,
        }
        return result


class BatchUnstableFixedRuntime(FixedRuntime):
    def __init__(self):
        self.calls = 0

    def evaluate(self, scenario, worker_mode):
        self.calls += 1
        result = super().evaluate(scenario, worker_mode)
        result["prediction"]["text"] = f"batch-{(self.calls - 1) // 2}"
        return result


class DeliveredExternalRuntime(FixedRuntime):
    def evaluate(self, scenario, worker_mode):
        result = super().evaluate(scenario, worker_mode)
        result["outbox"] = ({"correlation_id": "external:001", "part": {"idempotency_key": "delivery:001"}},)
        return result


class WrappedDeliveredExternalRuntime(FixedRuntime):
    def evaluate(self, scenario, worker_mode):
        result = super().evaluate(scenario, worker_mode)
        result["outbox"] = ({"correlation_id": "external:001", "bundle": {"parts": [{"part": {"idempotency_key": "delivery:001"}}]}},)
        return result


class InvalidLatencyRuntime(FixedRuntime):
    def __init__(self, latency):
        self.latency = latency

    def evaluate(self, scenario, worker_mode):
        result = super().evaluate(scenario, worker_mode)
        if self.latency is None:
            result.pop("latency_ms")
        else:
            result["latency_ms"] = self.latency
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
    with pytest.raises(ValueError, match="fixed worker report is not deterministic"):
        EvaluationRunner().run(
            (_scenario("social", lane="SOCIAL_CONVERSATION"),),
            UnstableFixedRuntime(),
            worker_mode="fixed",
        )


def test_fixed_worker_compares_complete_reports_not_only_each_scenario_replay():
    with pytest.raises(ValueError, match="fixed worker report is not deterministic"):
        EvaluationRunner().run(
            (
                _scenario("social-1", lane="SOCIAL_CONVERSATION"),
                _scenario("social-2", lane="SOCIAL_CONVERSATION"),
            ),
            BatchUnstableFixedRuntime(),
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


def test_missing_ownership_and_incomplete_member_context_are_excluded_fail_closed():
    missing_owner = _scenario("missing-owner", lane="SOCIAL_CONVERSATION")
    missing_owner.pop("ownership")
    bot_only = _scenario("bot-only", lane="SOCIAL_CONVERSATION")
    bot_only["context_provenance"] = {"complete_member_context": False, "bot_only": True}

    report = EvaluationRunner().run((missing_owner, bot_only), FixedRuntime(), worker_mode="fixed")

    assert report.excluded_unknown_count == 1
    assert report.lanes["SOCIAL_CONVERSATION"].effect_count == 1
    assert report.lanes["SOCIAL_CONVERSATION"].metrics["attention"] is None
    assert report.lanes["SOCIAL_CONVERSATION"].metrics["style_applicable"] is True


def test_unvalidated_candidate_producer_is_excluded_fail_closed():
    scenario = _scenario("unvalidated-producer", lane="SOCIAL_CONVERSATION")
    scenario["candidate_producer"] = "UNKNOWN"

    report = EvaluationRunner().run((scenario,), FixedRuntime(), worker_mode="fixed")

    assert report.excluded_unknown_count == 1


def test_external_compatibility_counts_groupmate_non_interference_as_correct_negative():
    scenario = _scenario("external", lane="EXTERNAL_PLUGIN_COMPATIBILITY")
    scenario["external_response_owner"] = "EXTERNAL_PLUGIN"

    report = EvaluationRunner().run((scenario,), FixedRuntime(), worker_mode="fixed")

    assert report.lanes["EXTERNAL_PLUGIN_COMPATIBILITY"].compatibility == {
        "no_steal": 1,
        "no_duplicate": 1,
        "no_self_attribution": 1,
    }


def test_external_compatibility_fails_when_actual_groupmate_outbox_claims_external_response():
    scenario = _scenario("external-delivery", lane="EXTERNAL_PLUGIN_COMPATIBILITY")
    scenario["external_response_owner"] = "EXTERNAL_PLUGIN"
    scenario["external_response_correlation"] = "external:001"

    report = EvaluationRunner().run((scenario,), DeliveredExternalRuntime(), worker_mode="fixed")

    assert report.lanes["EXTERNAL_PLUGIN_COMPATIBILITY"].compatibility == {
        "no_steal": 0,
        "no_duplicate": 0,
        "no_self_attribution": 0,
    }


def test_external_compatibility_resolves_parent_correlation_for_nested_delivery_part():
    scenario = _scenario("external-wrapped-delivery", lane="EXTERNAL_PLUGIN_COMPATIBILITY")
    scenario["external_response_owner"] = "EXTERNAL_PLUGIN"
    scenario["external_response_correlation"] = "external:001"

    report = EvaluationRunner().run((scenario,), WrappedDeliveredExternalRuntime(), worker_mode="fixed")

    assert report.lanes["EXTERNAL_PLUGIN_COMPATIBILITY"].compatibility == {
        "no_steal": 0,
        "no_duplicate": 0,
        "no_self_attribution": 0,
    }


def test_live_mode_rejects_incomplete_model_facts_and_uses_nearest_rank_p95():
    with pytest.raises(ValueError, match="live model facts"):
        EvaluationRunner().run(
            (_scenario("social", lane="SOCIAL_CONVERSATION"),),
            IncompleteLiveRuntime(),
            worker_mode="live",
        )
    with pytest.raises(ValueError, match="live model facts"):
        EvaluationRunner().run(
            (_scenario("social", lane="SOCIAL_CONVERSATION"),),
            InvalidLiveRuntime(),
            worker_mode="live",
        )

    class LatencyRuntime(FixedRuntime):
        def evaluate(self, scenario, worker_mode):
            result = super().evaluate(scenario, worker_mode)
            result["latency_ms"] = int(scenario["scenario_id"])
            return result

    report = EvaluationRunner().run(
        tuple(_scenario(str(value), lane="SOCIAL_CONVERSATION") for value in range(1, 6)),
        LatencyRuntime(),
        worker_mode="fixed",
    )
    assert report.latency_ms["p95"] == 5.0


def test_readiness_requires_installed_frozen_live_shadow_manifest_with_fixed_digests():
    corpus = [
        _scenario("cal", lane="SOCIAL_CONVERSATION"),
        _scenario("hold", lane="SOCIAL_CONVERSATION"),
    ]
    corpus[0]["split"] = "calibration"
    corpus[1]["split"] = "holdout"
    for record in corpus:
        record["labels_frozen"] = True

    assert EvaluationRunner().run(corpus, FixedRuntime(), worker_mode="fixed").production_readiness_eligible is False

    scenario_digest = hashlib.sha256(json.dumps(
        [{"scenario_id": item["scenario_id"], "split": item["split"], "evaluation_lane": item["evaluation_lane"]} for item in corpus],
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()
    label_digest = hashlib.sha256(json.dumps(
        [{"scenario_id": item["scenario_id"], "label": item["label"]} for item in corpus],
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()
    provenance = {
        "kind": "installed_live_shadow",
        "manifest_version": 1,
        "installed": True,
        "runtime_mode": "SHADOW",
        "frozen": True,
        "scenario_digest": scenario_digest,
        "label_digest": label_digest,
    }
    for record in corpus:
        record["shadow_provenance"] = provenance

    assert EvaluationRunner().run(corpus, FixedRuntime(), worker_mode="fixed").production_readiness_eligible is True


@pytest.mark.parametrize("latency", (-1, float("nan"), float("inf"), None))
def test_every_runtime_result_requires_finite_non_negative_latency(latency):
    with pytest.raises(ValueError, match="runtime latency_ms"):
        EvaluationRunner().run(
            (_scenario("latency", lane="SOCIAL_CONVERSATION"),),
            InvalidLatencyRuntime(latency),
            worker_mode="fixed",
        )


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
