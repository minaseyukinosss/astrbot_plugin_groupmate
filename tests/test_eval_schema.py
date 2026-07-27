import json
from pathlib import Path

import pytest

from eval.schema import (
    SCHEMA_VERSION,
    Scenario,
    ScenarioValidationError,
    compute_prompt_version,
    load_scenarios,
)


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "eval" / "scenarios" / "baseline.jsonl"


def valid_scenario():
    return {
        "schema_version": SCHEMA_VERSION,
        "id": "schema-valid-01",
        "category": "single_turn",
        "description": "合法合成场景",
        "messages": [
            {
                "message_id": "m1",
                "group_id": "g1",
                "sender_id": "u1",
                "sender_name": "群友甲",
                "text": "爱弥斯",
                "timestamp": 100,
            }
        ],
        "expected": {
            "trigger": "alias_direct",
            "action": "sent",
        },
        "scripted": {"output": "在呢。"},
        "constraints": {"max_chars": 60},
    }


def test_scenario_schema_accepts_valid_synthetic_fixture():
    scenario = Scenario.from_dict(valid_scenario())

    assert scenario.scenario_id == "schema-valid-01"
    assert scenario.topic_snapshot().latest.sender_id == "u1"
    assert scenario.group_policy().humanize_delay_enabled is False


def test_scenario_schema_rejects_unknown_version_and_fields():
    wrong_version = valid_scenario()
    wrong_version["schema_version"] = 999
    with pytest.raises(ScenarioValidationError, match="schema_version"):
        Scenario.from_dict(wrong_version)

    unknown = valid_scenario()
    unknown["unexpected"] = True
    with pytest.raises(ScenarioValidationError, match="unknown fields"):
        Scenario.from_dict(unknown)


def test_scenario_schema_rejects_real_numeric_ids():
    raw = valid_scenario()
    raw["messages"][0]["sender_id"] = "123456789"

    with pytest.raises(ScenarioValidationError, match="real numeric account ID"):
        Scenario.from_dict(raw)


def test_load_scenarios_rejects_duplicate_ids(tmp_path):
    raw = valid_scenario()
    corpus = tmp_path / "duplicate.jsonl"
    line = json.dumps(raw, ensure_ascii=False)
    corpus.write_text(line + "\n" + line + "\n", encoding="utf-8")

    with pytest.raises(ScenarioValidationError, match="duplicates scenario id"):
        load_scenarios(corpus)


def test_prompt_version_is_stable_and_excludes_secrets(tmp_path):
    persona = tmp_path / "persona.md"
    persona.write_text("爱弥斯", encoding="utf-8")

    first = compute_prompt_version(
        [persona],
        model_config={"model": "demo", "api_key": "secret-a"},
    )
    second = compute_prompt_version(
        [persona],
        model_config={"api_key": "secret-b", "model": "demo"},
    )

    assert first == second
    assert "secret" not in first


def test_baseline_contains_120_unique_privacy_safe_scenarios():
    scenarios = load_scenarios(BASELINE)

    assert len(scenarios) == 120
    assert len({scenario.scenario_id for scenario in scenarios}) == 120
    assert all(
        message.sender_id.startswith(("u", "bot"))
        for scenario in scenarios
        for message in scenario.messages
    )
    assert all(
        not scenario.model_enabled
        for scenario in scenarios
        if scenario.category == "guard"
    )
