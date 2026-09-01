from __future__ import annotations

from types import SimpleNamespace

from eval.knowledge import evaluate_canary_rollout
from groupmate.social_runtime.knowledge.enrichment import AmbientBudget


def _window(**overrides):
    value = {
        "observation_hours": 168,
        "opportunities": 1_000,
        "ambient_actions": 100,
        "ambient_searches": 20,
        "p95_latency_ms": 80,
        "silence_reasons": {"low_value": 300},
        "unsupported_claims": 0,
        "stale_scene_sends": 0,
        "cross_group_leaks": 0,
        "nonknowledge_ambient_searches": 0,
        "provider_quota_anomalies": 0,
    }
    value.update(overrides)
    return value


def test_ambient_canary_rolls_back_if_nonknowledge_search_appears():
    baseline = _window()
    clean_day = _window(observation_hours=24, opportunities=200, ambient_actions=21)
    unsafe_day = _window(
        observation_hours=24,
        opportunities=200,
        ambient_actions=21,
        nonknowledge_ambient_searches=1,
    )

    assert evaluate_canary_rollout(baseline, clean_day)["decision"] == "expand"
    result = evaluate_canary_rollout(baseline, unsafe_day)
    assert result["decision"] == "rollback"
    assert result["reason_codes"] == ("nonknowledge_ambient_search",)


def test_ambient_budget_uses_attention_and_intention_deadlines():
    evaluation = SimpleNamespace(
        frame=SimpleNamespace(deadline=108),
        governor_result=SimpleNamespace(
            outcome="ACT", selected_intention_ids=("intent:1",)
        ),
        candidates=(
            SimpleNamespace(intention_id="intent:1", expires_at=102.249),
        ),
    )

    insufficient = AmbientBudget.from_evaluation(evaluation, now=100)
    eligible = AmbientBudget.from_evaluation(
        SimpleNamespace(
            frame=evaluation.frame,
            governor_result=evaluation.governor_result,
            candidates=(
                SimpleNamespace(intention_id="intent:1", expires_at=102.25),
            ),
        ),
        now=100,
    )

    assert insufficient.remaining_ms == 2249
    assert insufficient.can_search is False
    assert eligible.remaining_ms == 2250
    assert eligible.can_search is True
    assert eligible.provider_timeout_ms == 2000
