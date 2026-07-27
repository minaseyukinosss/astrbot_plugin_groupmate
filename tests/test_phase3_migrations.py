"""Phase 3 schema migration: social_events + relationship_state."""

from __future__ import annotations

import sqlite3

import pytest

import groupmate.memory.migrations as migrations
from groupmate.memory.migrations import (
    SchemaTooNewError,
    _bootstrap_v5,
    _v5_to_v6,
    _v6_to_v7,
    migrate_database,
)
from groupmate.memory.store import SQLiteMemoryStore


def test_v7_database_migrates_to_v8_with_affinity_backfill(tmp_path):
    path = tmp_path / "legacy.db"
    db = sqlite3.connect(str(path))
    with db:
        _bootstrap_v5(db)
        _v5_to_v6(db)
        _v6_to_v7(db)
        db.execute("UPDATE schema_meta SET value='7' WHERE key='version'")
        db.execute(
            "INSERT INTO favorability(group_id, user_id, score, updated_at) "
            "VALUES ('g', 'u1', 80, 10)"
        )
    db.close()

    store = SQLiteMemoryStore(path)
    assert store.schema_version() == 9
    state = store.get_relationship_state("g", "u1")
    assert state is not None
    assert state.affinity == 80
    assert store.list_social_events("g") == []
    store.close()
    assert list(tmp_path.glob("legacy.db.pre-migrate-v7-to-v9.*"))


def test_failed_v8_migration_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / "broken.db"
    db = sqlite3.connect(str(path))
    with db:
        _bootstrap_v5(db)
        _v5_to_v6(db)
        _v6_to_v7(db)
        db.execute("UPDATE schema_meta SET value='7' WHERE key='version'")
    db.close()

    def broken(db):
        db.execute(
            "CREATE TABLE IF NOT EXISTS social_events (event_id TEXT PRIMARY KEY)"
        )
        raise RuntimeError("boom")

    monkeypatch.setattr(migrations, "_v7_to_v8", broken)
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
    assert version == "7"
    assert "relationship_state" not in tables
    assert list(tmp_path.glob("broken.db.pre-migrate-v7-to-v9.*"))


def test_newer_database_is_rejected(tmp_path):
    path = tmp_path / "future.db"
    db = sqlite3.connect(str(path))
    db.execute("CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("INSERT INTO schema_meta VALUES ('version', '99')")
    db.commit()
    db.close()
    with pytest.raises(SchemaTooNewError):
        SQLiteMemoryStore(path)
