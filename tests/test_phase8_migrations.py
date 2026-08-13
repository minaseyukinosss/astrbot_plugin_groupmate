import sqlite3

from groupmate.memory.migrations import (
    SCHEMA_VERSION,
    _bootstrap_v11,
    _v11_to_v12,
    _v12_to_v13,
    _v13_to_v14,
    _v14_to_v15,
    _v15_to_v16,
)
from groupmate.memory.store import SQLiteMemoryStore


def test_v16_database_migrates_to_current_with_self_commitments(tmp_path):
    path = tmp_path / "legacy-v16.db"
    db = sqlite3.connect(str(path))
    with db:
        _bootstrap_v11(db)
        _v11_to_v12(db)
        _v12_to_v13(db)
        _v13_to_v14(db)
        _v14_to_v15(db)
        _v15_to_v16(db)
        db.execute("UPDATE schema_meta SET value='16' WHERE key='version'")
    db.close()

    store = SQLiteMemoryStore(path)
    tables = {
        row[0]
        for row in store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert store.schema_version() == SCHEMA_VERSION
    assert "self_commitments" in tables
    columns = {
        row[1] for row in store._db.execute("PRAGMA table_info(self_commitments)")
    }
    assert {
        "request_message_id",
        "fulfillment_mode",
        "next_attempt_at",
        "attempt_count",
        "lease_owner",
        "lease_until",
        "last_attempt_at",
        "last_delivery_at",
    }.issubset(columns)
    store.close()
    assert list(tmp_path.glob(f"legacy-v16.db.pre-migrate-v16-to-v{SCHEMA_VERSION}.*"))
