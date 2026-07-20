import json

from groupmate.evaluation.cli import main
from tests.test_evaluation_dataset import valid_case, write_cases


def write_config(path):
    path.write_text(
        json.dumps(
            {
                "name": "安全沉默基线",
                "aliases": ["小爱"],
                "decision_threshold": 0.72,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_validate_command_accepts_valid_dataset(tmp_path):
    dataset = write_cases(tmp_path / "cases.jsonl", [valid_case()])
    assert main(["validate", "--dataset", str(dataset)]) == 0


def test_run_writes_json_and_chinese_markdown(tmp_path):
    dataset = write_cases(tmp_path / "cases.jsonl", [valid_case()])
    config = write_config(tmp_path / "config.json")
    output = tmp_path / "output"
    assert (
        main(
            [
                "run",
                "--dataset",
                str(dataset),
                "--config",
                str(config),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert result["dataset_hash"]
    assert result["predictions"][0]["action"] == "respond"
    assert "样本不足" in (output / "report.md").read_text(encoding="utf-8")


def test_compare_rejects_different_dataset_hashes(tmp_path):
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    baseline.write_text(json.dumps({"dataset_hash": "a"}), encoding="utf-8")
    candidate.write_text(json.dumps({"dataset_hash": "b"}), encoding="utf-8")
    assert (
        main(
            [
                "compare",
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
                "--output",
                str(tmp_path / "compare.md"),
            ]
        )
        == 2
    )


def test_compare_lists_changed_cases(tmp_path):
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    common = {"dataset_hash": "same", "metrics": {}, "config": {}}
    baseline.write_text(
        json.dumps(
            dict(
                common,
                predictions=[{"case_id": "c1", "matched": False, "action": "ignore"}],
            )
        ),
        encoding="utf-8",
    )
    candidate.write_text(
        json.dumps(
            dict(
                common,
                predictions=[{"case_id": "c1", "matched": True, "action": "respond"}],
            )
        ),
        encoding="utf-8",
    )
    output = tmp_path / "compare.md"
    assert (
        main(
            [
                "compare",
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert "c1" in output.read_text(encoding="utf-8")
