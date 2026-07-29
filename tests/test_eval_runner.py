import asyncio
import json
from dataclasses import replace
from pathlib import Path

from eval.runner import run_evaluation, write_report
from eval.schema import ScenarioExpected, load_scenarios


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "eval" / "scenarios" / "baseline.jsonl"


def run_async(awaitable):
    try:
        return asyncio.run(awaitable)
    finally:
        # Python 3.7's asyncio.Lock constructor still asks the policy for a
        # current loop. Keep later synchronous constructor tests isolated.
        asyncio.set_event_loop(asyncio.new_event_loop())


def scenarios_by_id():
    return {scenario.scenario_id: scenario for scenario in load_scenarios(BASELINE)}


def test_deterministic_runner_covers_trigger_silence_guard_and_multi_turn():
    items = scenarios_by_id()
    selected = [
        items["trigger-prefix-01"],
        items["trigger-candidate-01"],
        items["guard-reject-01"],
        items["multi-retention-01"],
    ]

    report = run_async(run_evaluation(selected, mode="deterministic"))

    assert report["scenario_count"] == 4
    assert report["summary"]["errors"] == 0
    assert report["summary"]["passed_runs"] == 4
    results = {item["scenario_id"]: item for item in report["results"]}
    assert results["trigger-prefix-01"]["trigger"] == "alias_direct"
    assert results["trigger-candidate-01"]["outcome_reason"] == "no_open_motive"
    assert "decision_narration" in results["guard-reject-01"]["guard_codes"]
    multi_checks = {
        item["name"]: item for item in results["multi-retention-01"]["checks"]
    }
    assert multi_checks["conversation_context_retention"]["passed"] is True
    assert multi_checks["conversation_topic_continuity"]["passed"] is True


def test_quality_failure_is_reported_without_becoming_runtime_error():
    source = scenarios_by_id()["trigger-candidate-01"]
    mismatched = replace(
        source,
        expected=ScenarioExpected(
            trigger="candidate",
            action="sent",
            outcome_reason="sent",
        ),
    )

    report = run_async(run_evaluation([mismatched], mode="deterministic"))

    assert report["summary"]["errors"] == 0
    assert report["summary"]["passed_runs"] == 0
    assert report["results"][0]["error"] is None
    assert report["results"][0]["passed"] is False


def test_model_mode_skips_script_only_guard_scenarios(monkeypatch):
    class FakeConfig:
        def public_dict(self):
            return {
                "base_url": "https://example.invalid/v1",
                "model": "fake",
                "timeout_seconds": 1,
                "temperature": 0,
            }

    class FakeClient:
        def __init__(self, config):
            del config

        def complete(self, **kwargs):
            del kwargs
            return "在呢。"

    monkeypatch.setattr("eval.runner.OpenAICompatibleClient", FakeClient)
    selected = [
        scenarios_by_id()["guard-accept-01"],
        scenarios_by_id()["trigger-prefix-01"],
    ]

    report = run_async(
        run_evaluation(selected, mode="model", model_config=FakeConfig())
    )

    assert report["scenario_count"] == 1
    assert report["results"][0]["scenario_id"] == "trigger-prefix-01"


def test_report_writer_emits_machine_readable_json(tmp_path):
    scenario = scenarios_by_id()["trigger-prefix-01"]
    report = run_async(run_evaluation([scenario], mode="deterministic"))
    target = tmp_path / "result.json"

    write_report(report, target)
    decoded = json.loads(target.read_text(encoding="utf-8"))

    assert decoded["schema_version"] == 1
    assert decoded["summary"]["total_runs"] == 1
    assert decoded["prompt_version"] == report["prompt_version"]


def test_full_deterministic_baseline_is_internally_consistent():
    report = run_async(
        run_evaluation(load_scenarios(BASELINE), mode="deterministic")
    )

    assert report["scenario_count"] == 120
    assert report["summary"]["errors"] == 0
    assert report["summary"]["passed_runs"] == 120
