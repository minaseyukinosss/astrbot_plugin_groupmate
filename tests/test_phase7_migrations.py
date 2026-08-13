import sqlite3

from groupmate.memory.migrations import (
    SCHEMA_VERSION,
    _bootstrap_v11,
    _v11_to_v12,
    _v12_to_v13,
    _v13_to_v14,
    _v14_to_v15,
)
from groupmate.memory.store import SQLiteMemoryStore


def test_v15_database_migrates_to_v16_with_continuity_items(tmp_path):
    path = tmp_path / "legacy-v15.db"
    db = sqlite3.connect(str(path))
    with db:
        _bootstrap_v11(db)
        _v11_to_v12(db)
        _v12_to_v13(db)
        _v13_to_v14(db)
        _v14_to_v15(db)
        db.execute("UPDATE schema_meta SET value='15' WHERE key='version'")
    db.close()

    store = SQLiteMemoryStore(path)
    tables = {
        row[0]
        for row in store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert store.schema_version() == SCHEMA_VERSION
    assert "continuity_items" in tables
    store.close()
    assert list(tmp_path.glob(f"legacy-v15.db.pre-migrate-v15-to-v{SCHEMA_VERSION}.*"))
