import json

import pytest

from groupmate.evaluation.dataset import DatasetValidationError, load_dataset
from groupmate.evaluation.models import EvaluationLabel


def valid_case(case_id="case-1"):
    return {
        "schema_version": 1,
        "case_id": case_id,
        "description": "直接呼叫别名",
        "messages": [
            {
                "message_id": "m1",
                "group_id": "eval-group",
                "sender_id": "u1",
                "sender_name": "群友甲",
                "text": "小爱，在吗",
                "timestamp": 1000,
                "image_urls": [],
                "segment_types": ["text"],
            }
        ],
        "expected": {
            "label": "must_respond",
            "allowed_triggers": ["alias_direct"],
            "allowed_reason_codes": ["alias_direct"],
            "target_message_id": "m1",
        },
        "tags": ["wake", "critical"],
        "source": "handcrafted",
    }


def write_cases(path, cases):
    path.write_text(
        "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases),
        encoding="utf-8",
    )
    return path


def test_loads_valid_dataset(tmp_path):
    dataset = load_dataset(write_cases(tmp_path / "cases.jsonl", [valid_case()]))
    assert dataset.cases[0].expected.label is EvaluationLabel.MUST_RESPOND
    assert dataset.cases[0].messages[0].text == "小爱，在吗"
    assert len(dataset.content_hash) == 64


def test_dataset_hash_is_independent_of_file_path(tmp_path):
    first = write_cases(tmp_path / "a.jsonl", [valid_case()])
    second = write_cases(tmp_path / "b.jsonl", [valid_case()])
    assert load_dataset(first).content_hash == load_dataset(second).content_hash


def test_duplicate_case_id_is_rejected(tmp_path):
    path = write_cases(tmp_path / "cases.jsonl", [valid_case(), valid_case()])
    with pytest.raises(DatasetValidationError, match="case_id 重复"):
        load_dataset(path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda case: case["messages"].append(
                dict(case["messages"][0], message_id="m2", group_id="other")
            ),
            "同一个群",
        ),
        (
            lambda case: case["messages"].append(
                dict(case["messages"][0], message_id="m2", timestamp=999)
            ),
            "时间戳",
        ),
        (lambda case: case["expected"].update(label="unknown"), "标签"),
    ],
)
def test_rejects_invalid_case(tmp_path, mutate, message):
    case = valid_case()
    mutate(case)
    path = write_cases(tmp_path / "cases.jsonl", [case])
    with pytest.raises(DatasetValidationError, match=message):
        load_dataset(path)
