import asyncio
import sqlite3

import pytest

import groupmate.memory.migrations as migrations
from groupmate.memory.migrations import (
    SchemaTooNewError,
    _bootstrap_v5,
)
from groupmate.memory.store import SQLiteMemoryStore


def test_v5_database_is_backed_up_and_migrated(tmp_path):
    path = tmp_path / "legacy.db"
    db = sqlite3.connect(str(path))
    with db:
        _bootstrap_v5(db)
        db.execute(
            "INSERT INTO outbox(decision_id, group_id, text, created_at, sent_at) "
            "VALUES ('sent-one', 'g', 'ok', 1, 2)"
        )
    db.close()

    store = SQLiteMemoryStore(path)
    assert store.schema_version() == 9
    assert store.outbox_record("sent-one")["status"] == "sent"
    store.close()
    assert list(tmp_path.glob("legacy.db.pre-migrate-v5-to-v9.*"))


def test_newer_database_is_rejected(tmp_path):
    path = tmp_path / "future.db"
    db = sqlite3.connect(str(path))
    db.execute("CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("INSERT INTO schema_meta VALUES ('version', '99')")
    db.commit()
    db.close()

    with pytest.raises(SchemaTooNewError):
        SQLiteMemoryStore(path)


def test_failed_migration_rolls_back_and_keeps_backup(tmp_path, monkeypatch):
    path = tmp_path / "broken.db"
    db = sqlite3.connect(str(path))
    with db:
        _bootstrap_v5(db)
    db.close()

    def broken(db):
        db.execute("ALTER TABLE outbox ADD COLUMN temporary_column TEXT")
        raise RuntimeError("boom")

    monkeypatch.setattr(migrations, "_v5_to_v6", broken)
    with pytest.raises(RuntimeError):
        migrations.migrate_database(path)

    db = sqlite3.connect(str(path))
    version = db.execute(
        "SELECT value FROM schema_meta WHERE key='version'"
    ).fetchone()[0]
    columns = {row[1] for row in db.execute("PRAGMA table_info(outbox)")}
    db.close()
    assert version == "5"
    assert "temporary_column" not in columns
    assert list(tmp_path.glob("broken.db.pre-migrate-v5-to-v9.*"))


def test_single_writer_serializes_concurrent_groups(tmp_path, message_factory):
    async def scenario():
        store = SQLiteMemoryStore(tmp_path / "concurrent.db")
        messages = [
            message_factory(
                group_id="g{}".format(index % 2),
                message_id=str(index),
                timestamp=index + 1,
            )
            for index in range(80)
        ]
        results = await asyncio.gather(
            *(store.save_message_async(message) for message in messages)
        )
        await store.flush_async()
        counts = (
            len(store.recent_messages("g0", 100)),
            len(store.recent_messages("g1", 100)),
        )
        store.close()
        return results, counts

    results, counts = asyncio.run(scenario())
    assert all(results)
    assert counts == (40, 40)
