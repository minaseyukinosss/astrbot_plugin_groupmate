"""Phase 5 schema migration: memory_candidates / memories 扩展 / tombstones。"""

from __future__ import annotations

import sqlite3

import pytest

import groupmate.memory.migrations as migrations
from groupmate.memory.migrations import (
    SCHEMA_VERSION,
    SchemaTooNewError,
    _bootstrap_v5,
    _v5_to_v6,
    _v6_to_v7,
    _v7_to_v8,
    migrate_database,
)
from groupmate.memory.store import SQLiteMemoryStore


def test_v8_database_migrates_to_v9_with_memory_backfill(tmp_path):
    path = tmp_path / "legacy.db"
    db = sqlite3.connect(str(path))
    with db:
        _bootstrap_v5(db)
        _v5_to_v6(db)
        _v6_to_v7(db)
        _v7_to_v8(db)
        db.execute("UPDATE schema_meta SET value='8' WHERE key='version'")
        db.execute(
            "INSERT INTO memories("
            "memory_id, group_id, subject_id, kind, text, created_at, "
            "expires_at, confidence, importance, authority, source_message_id"
            ") VALUES ('m1', 'g', 'u1', 'episodic', '喜欢猫', 10, NULL, 0.9, 0.5, 3, 'src-1')"
        )
    db.close()

    store = SQLiteMemoryStore(path)
    assert store.schema_version() == SCHEMA_VERSION
    item = store.get_memory("aemeath", "m1")
    assert item is not None
    assert item.status.value == "accepted"
    assert item.scope.value == "USER_IN_GROUP"
    assert item.source_message_ids == ("src-1",)
    tables = {
        row[0]
        for row in store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "memory_candidates" in tables
    assert "memory_tombstones" in tables
    store.close()
    assert list(tmp_path.glob(f"legacy.db.pre-migrate-v8-to-v{SCHEMA_VERSION}.*"))


def test_failed_v9_migration_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / "broken.db"
    db = sqlite3.connect(str(path))
    with db:
        _bootstrap_v5(db)
        _v5_to_v6(db)
        _v6_to_v7(db)
        _v7_to_v8(db)
        db.execute("UPDATE schema_meta SET value='8' WHERE key='version'")
    db.close()

    def broken(db):
        db.execute(
            "CREATE TABLE IF NOT EXISTS memory_candidates "
            "(candidate_id TEXT PRIMARY KEY)"
        )
        raise RuntimeError("boom")

    monkeypatch.setattr(migrations, "_v8_to_v9", broken)
    with pytest.raises(RuntimeError):
        migrate_database(path)

    db = sqlite3.connect(str(path))
    version = db.execute(
        "SELECT value FROM schema_meta WHERE key='version'"
    ).fetchone()[0]
    tables = {
        row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    db.close()
    assert version == "8"
    assert "memory_tombstones" not in tables
    assert list(tmp_path.glob(f"broken.db.pre-migrate-v8-to-v{SCHEMA_VERSION}.*"))


def test_newer_database_is_rejected(tmp_path):
    path = tmp_path / "future.db"
    db = sqlite3.connect(str(path))
    db.execute("CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("INSERT INTO schema_meta(key, value) VALUES('version', '99')")
    db.commit()
    db.close()
    with pytest.raises(SchemaTooNewError):
        migrate_database(path)
