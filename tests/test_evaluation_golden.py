import asyncio
from pathlib import Path

from groupmate.evaluation.cli import SafeSilenceDecisionModel
from groupmate.evaluation.dataset import load_dataset
from groupmate.evaluation.evaluator import DecisionEvaluator
from groupmate.evaluation.models import EvaluationLabel
from groupmate.models import GroupPolicy


GOLDEN_PATH = Path(__file__).parent / "fixtures" / "evaluation" / "golden.jsonl"


def test_golden_dataset_has_required_coverage():
    dataset = load_dataset(GOLDEN_PATH)
    assert len(dataset.cases) >= 30
    tags = {tag for case in dataset.cases for tag in case.tags}
    assert {"wake", "command", "silence", "ordinary"} <= tags
    assert all(case.source == "handcrafted" for case in dataset.cases)


def test_safe_baseline_matches_all_strict_golden_cases():
    dataset = load_dataset(GOLDEN_PATH)
    evaluator = DecisionEvaluator(SafeSilenceDecisionModel(), GroupPolicy())
    predictions = [asyncio.run(evaluator.evaluate(case)) for case in dataset.cases]
    strict = [
        prediction
        for prediction in predictions
        if prediction.expected_label is not EvaluationLabel.MAY_RESPOND
    ]
    assert strict
    assert all(prediction.matched for prediction in strict)
