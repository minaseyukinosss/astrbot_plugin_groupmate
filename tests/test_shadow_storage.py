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
    assert stats["recent"][0]["decision_id"] == "d1"
    assert "context" not in str(stats)
    store.close()


def test_recent_shadow_decisions_are_group_scoped_and_newest_first(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.save_shadow_decision(
        record(decision_id="a-old", group_hash="group-a", created_at=10)
    )
    store.save_shadow_decision(
        record(decision_id="other", group_hash="group-b", created_at=30)
    )
    store.save_shadow_decision(
        record(
            decision_id="a-new",
            group_hash="group-a",
            created_at=20,
            context=[
                {"sender": "成员1", "text": "较早消息"},
                {"sender": "成员2", "text": "最新消息"},
            ],
        )
    )

    rows = store.recent_shadow_decisions("group-a", 10)

    assert [row["decision_id"] for row in rows] == ["a-new", "a-old"]
    assert rows[0]["latest_message"] == {
        "sender": "成员2",
        "text": "最新消息",
    }
    assert rows[1]["latest_message"] is None
    assert not {
        "group_hash",
        "sender_hash",
        "features_json",
        "context_json",
        "model_id",
        "latency_ms",
        "error_code",
    }.intersection(rows[0])
    store.close()


def test_recent_shadow_decisions_clamp_limit_at_storage_boundary(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    for index in range(12):
        store.save_shadow_decision(
            record(
                decision_id="d{}".format(index),
                group_hash="group-a",
                created_at=index,
            )
        )

    assert len(store.recent_shadow_decisions("group-a", 50)) == 10
    assert len(store.recent_shadow_decisions("group-a", 0)) == 1
    assert store.recent_shadow_decisions("group-a", 0)[0]["decision_id"] == "d11"
    store.close()


def test_recent_shadow_decisions_ignore_malformed_context_per_row(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.save_shadow_decision(
        record(
            decision_id="valid",
            group_hash="group-a",
            created_at=10,
            context=[
                {"sender": "成员1", "text": "保留"},
                {"sender": "成员2", "text": "   "},
                "not-a-message",
            ],
        )
    )
    store.save_shadow_decision(
        record(decision_id="broken", group_hash="group-a", created_at=20)
    )
    with store._db:
        store._db.execute(
            "UPDATE shadow_decisions SET context_json = ? WHERE decision_id = ?",
            ("{not-json", "broken"),
        )

    rows = store.recent_shadow_decisions("group-a", 5)

    assert rows[0]["decision_id"] == "broken"
    assert rows[0]["latest_message"] is None
    assert rows[1]["latest_message"] == {"sender": "成员1", "text": "保留"}
    store.close()


def test_recent_shadow_decisions_treat_deep_context_json_as_unavailable(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.save_shadow_decision(
        record(decision_id="deep", group_hash="group-a", created_at=10)
    )
    with store._db:
        store._db.execute(
            "UPDATE shadow_decisions SET context_json = ? WHERE decision_id = ?",
            ("[" * 1000 + "0" + "]" * 1000, "deep"),
        )

    rows = store.recent_shadow_decisions("group-a", 5)

    assert rows == [
        {
            "decision_id": "deep",
            "trigger": "candidate",
            "action": "ignore",
            "confidence": 0.2,
            "reason_code": "not_useful",
            "would_rate_limit": 0,
            "label": "unlabeled",
            "created_at": 10,
            "latest_message": None,
        }
    ]
    store.close()
