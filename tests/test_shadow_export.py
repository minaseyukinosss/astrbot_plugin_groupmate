import json

from groupmate.evaluation.cli import main
from groupmate.evaluation.dataset import load_dataset
from groupmate.evaluation.models import ShadowRecord
from groupmate.evaluation.shadow_export import export_labeled_shadow_dataset
from groupmate.memory import SQLiteMemoryStore


def record():
    return ShadowRecord(
        decision_id="d1",
        group_hash="a" * 64,
        sender_hash="b" * 64,
        trigger="candidate",
        action="ignore",
        confidence=0.4,
        reason_code="not_useful",
        would_rate_limit=False,
        features={"message_count": 2},
        context=[
            {
                "index": 1,
                "sender": "成员1",
                "text": "今天好热",
                "seconds_from_start": 0,
                "reply": False,
                "mentions_bot": False,
                "reply_to_bot": False,
                "is_command": False,
                "is_bot": False,
                "segment_types": ["text"],
            },
            {
                "index": 2,
                "sender": "成员2",
                "text": "确实",
                "seconds_from_start": 1,
                "reply": False,
                "mentions_bot": False,
                "reply_to_bot": False,
                "is_command": False,
                "is_bot": False,
                "segment_types": ["text"],
            },
        ],
        model_id="judge",
        policy_version="1",
        latency_ms=10,
        error_code=None,
        created_at=100,
        expires_at=1000,
    )


def test_exports_locally_labeled_shadow_rows_as_valid_dataset(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.save_shadow_decision(record())
    store.label_shadow_decision("d1", "must_silence", 200)
    output = tmp_path / "reviewed.jsonl"
    assert export_labeled_shadow_dataset(store, output) == 1
    dataset = load_dataset(output)
    assert dataset.cases[0].source == "shadow_reviewed"
    assert dataset.cases[0].expected.label.value == "must_silence"
    encoded = output.read_text(encoding="utf-8")
    assert "今天好热" in encoded
    assert "a" * 64 not in encoded
    assert "b" * 64 not in encoded
    store.close()


def test_cli_exports_from_plugin_database(tmp_path):
    database = tmp_path / "memory.db"
    store = SQLiteMemoryStore(database)
    store.save_shadow_decision(record())
    store.label_shadow_decision("d1", "must_silence", 200)
    store.close()
    output = tmp_path / "reviewed.jsonl"
    assert (
        main(
            [
                "export-shadow",
                "--database",
                str(database),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert json.loads(output.read_text(encoding="utf-8"))["case_id"] == "shadow-d1"
