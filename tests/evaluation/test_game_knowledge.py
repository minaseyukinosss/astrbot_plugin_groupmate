from __future__ import annotations

import importlib

import pytest


def _metrics(records):
    module = importlib.import_module("eval.knowledge")
    return module.game_knowledge_metrics(records)


def _record(**overrides):
    value = {
        "understanding_correct": True,
        "high_confidence_merge": False,
        "merge_correct": True,
        "cross_group_leak": False,
        "promoted_origin": None,
        "temporal_need_expected": False,
        "temporal_need_detected": False,
    }
    value.update(overrides)
    return value


def test_metrics_count_unsafe_promotions_and_measure_temporal_recall():
    result = _metrics(
        (
            _record(
                high_confidence_merge=True,
                merge_correct=False,
                promoted_origin="external_bot",
                temporal_need_expected=True,
                temporal_need_detected=True,
            ),
            _record(
                understanding_correct=False,
                high_confidence_merge=True,
                merge_correct=True,
                cross_group_leak=True,
                promoted_origin="command",
                temporal_need_expected=True,
                temporal_need_detected=False,
            ),
            _record(promoted_origin="human_chat"),
        )
    )

    assert result["understanding_accuracy"] == pytest.approx(2 / 3)
    assert result["high_confidence_wrong_merge_rate"] == 0.5
    assert result["cross_group_leaks"] == 1
    assert result["bot_promotions"] == 1
    assert result["command_promotions"] == 1
    assert result["temporal_need_recall"] == 0.5


def test_metrics_return_zero_for_empty_denominators():
    result = _metrics((_record(),))

    assert result == {
        "understanding_accuracy": 1.0,
        "high_confidence_wrong_merge_rate": 0.0,
        "cross_group_leaks": 0,
        "bot_promotions": 0,
        "command_promotions": 0,
        "temporal_need_recall": 0.0,
    }


@pytest.mark.parametrize(
    "record",
    [
        {},
        _record(understanding_correct=1),
        _record(promoted_origin="unclassified_feed"),
        _record(temporal_need_expected=False, temporal_need_detected=True),
    ],
)
def test_metrics_reject_malformed_records(record):
    with pytest.raises(ValueError, match="record"):
        _metrics((record,))
