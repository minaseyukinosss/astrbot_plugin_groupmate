"""Phase 6 schema migration adds ordered outbound metadata."""

import sqlite3

from groupmate.memory.migrations import (
    _bootstrap_v5,
    _v5_to_v6,
    _v6_to_v7,
    _v7_to_v8,
    _v8_to_v9,
)
from groupmate.memory.store import SQLiteMemoryStore


def test_v9_database_migrates_to_v10_with_outbound_json(tmp_path):
    path = tmp_path / "legacy-v9.db"
    db = sqlite3.connect(str(path))
    with db:
        _bootstrap_v5(db)
        _v5_to_v6(db)
        _v6_to_v7(db)
        _v7_to_v8(db)
        _v8_to_v9(db)
        db.execute("UPDATE schema_meta SET value='9' WHERE key='version'")
        db.execute(
            "INSERT INTO outbox("
            "decision_id, group_id, text, created_at, status, segments_json, kind"
            ") VALUES ('legacy', 'g', '在呢。', 1, 'pending', '[\"在呢。\"]', 'reply')"
        )
    db.close()

    store = SQLiteMemoryStore(path)
    columns = {
        row[1] for row in store._db.execute("PRAGMA table_info(outbox)").fetchall()
    }
    row = store.outbox_record("legacy")

    assert store.schema_version() == 10
    assert "outbound_json" in columns
    assert row["outbound_json"] == "[]"
    store.close()
    assert list(tmp_path.glob("legacy-v9.db.pre-migrate-v9-to-v10.*"))
