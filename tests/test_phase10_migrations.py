import sqlite3

from groupmate.memory.migrations import (
    SCHEMA_VERSION,
    _bootstrap_v11,
    _v11_to_v12,
    _v12_to_v13,
    _v13_to_v14,
    _v14_to_v15,
    _v15_to_v16,
    _v16_to_v17,
    _v17_to_v18,
)
from groupmate.memory.store import SQLiteMemoryStore


def test_v18_database_migrates_to_v19_followup_evidence(tmp_path):
    path = tmp_path / "legacy-v18.db"
    db = sqlite3.connect(str(path))
    with db:
        _bootstrap_v11(db)
        _v11_to_v12(db)
        _v12_to_v13(db)
        _v13_to_v14(db)
        _v14_to_v15(db)
        _v15_to_v16(db)
        _v16_to_v17(db)
        _v17_to_v18(db)
        db.execute("UPDATE schema_meta SET value='18' WHERE key='version'")
    db.close()

    store = SQLiteMemoryStore(path)
    try:
        assert store.schema_version() == SCHEMA_VERSION == 21
        columns = {
            row[1]
            for row in store._db.execute(
                "PRAGMA table_info(continuity_followup_events)"
            )
        }
        assert {"event_id", "item_id", "response_policy", "sent"}.issubset(
            columns
        )
        tables = {
            row[0]
            for row in store._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"fun_feature_events", "fun_feature_state"}.issubset(tables)
    finally:
        store.close()
    assert list(tmp_path.glob("legacy-v18.db.pre-migrate-v18-to-v21.*"))
