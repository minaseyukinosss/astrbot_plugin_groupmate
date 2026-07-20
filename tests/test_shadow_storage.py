import sqlite3

from groupmate.evaluation.models import ShadowRecord
from groupmate.memory import SQLiteMemoryStore


def record(**overrides):
    values = {
        "decision_id": "d1",
        "group_hash": "group-hash",
        "sender_hash": "sender-hash",
        "trigger": "candidate",
        "action": "ignore",
        "confidence": 0.2,
        "reason_code": "not_useful",
        "would_rate_limit": False,
        "features": {"message_count": 2},
        "context": None,
        "model_id": "model-a",
        "policy_version": "1",
        "latency_ms": 12.5,
        "error_code": None,
        "created_at": 10,
        "expires_at": 20,
    }
    values.update(overrides)
    return ShadowRecord(**values)


def test_migrates_version_one_database(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(str(path))
    db.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("INSERT INTO schema_meta VALUES ('version', '1')")
    db.commit()
    db.close()
    store = SQLiteMemoryStore(path)
    assert store.schema_version() == 2
    assert store.shadow_count() == 0
    store.close()


def test_shadow_record_is_idempotent_and_private_by_default(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    assert store.save_shadow_decision(record()) is True
    assert store.save_shadow_decision(record()) is False
    row = store.get_shadow_decision("d1")
    assert row["group_hash"] == "group-hash"
    assert row["context_json"] is None
    assert row["label"] == "unlabeled"
    store.close()


def test_label_does_not_change_prediction(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.save_shadow_decision(record(action="ignore"))
    assert store.label_shadow_decision("d1", "must_respond", 20) is True
    row = store.get_shadow_decision("d1")
    assert row["action"] == "ignore"
    assert row["label"] == "must_respond"
    assert row["labeled_at"] == 20
    store.close()


def test_expired_shadow_records_are_purged(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.save_shadow_decision(record(decision_id="old", expires_at=20))
    store.save_shadow_decision(record(decision_id="new", expires_at=40))
    assert store.purge_expired_shadow(now=30) == 1
    assert store.get_shadow_decision("old") is None
    assert store.shadow_count() == 1
    store.close()


def test_shadow_stats_do_not_return_context(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.save_shadow_decision(record(context=[{"sender": "成员1", "text": "你好"}]))
    stats = store.shadow_stats()
    assert stats["total"] == 1
    assert stats["actions"] == {"ignore": 1}
    assert "context" not in str(stats)
    store.close()
