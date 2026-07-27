"""Phase 2 schema migration tests."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

import groupmate.memory.migrations as migrations
from groupmate.memory.migrations import (
    SchemaTooNewError,
    _bootstrap_v5,
    _v5_to_v6,
    migrate_database,
)
from groupmate.memory.store import SQLiteMemoryStore
from groupmate.models import ChatMessage, MessageOrigin


def test_v6_database_migrates_to_v7_with_backfill(tmp_path):
    path = tmp_path / "legacy.db"
    db = sqlite3.connect(str(path))
    with db:
        _bootstrap_v5(db)
        _v5_to_v6(db)
        db.execute("UPDATE schema_meta SET value='6' WHERE key='version'")
        db.execute(
            "INSERT INTO messages("
            "group_id, message_id, sender_id, sender_name, text, timestamp, "
            "reply_to_message_id, reply_to_bot, mentions_bot, is_bot, is_command, "
            "image_urls, segment_types, metadata"
            ") VALUES ('g', 'bot-1', '__bot__', '爱弥斯', '在呢。', 10, "
            "NULL, 0, 0, 1, 0, '[]', '[\"text\"]', "
            "'{\"origin\":\"bot_delivery\",\"decision_id\":\"d1\"}')"
        )
        db.execute(
            "INSERT INTO messages("
            "group_id, message_id, sender_id, sender_name, text, timestamp, "
            "reply_to_message_id, reply_to_bot, mentions_bot, is_bot, is_command, "
            "image_urls, segment_types, metadata"
            ") VALUES ('g', 'u1', 'u1', 'Alice', '你好', 9, "
            "NULL, 0, 0, 0, 0, '[]', '[\"text\"]', '{}')"
        )
    db.close()

    store = SQLiteMemoryStore(path)
    assert store.schema_version() == 9
    messages = store.list_ledger_messages("g", limit=10)
    by_id = {item.message_id: item for item in messages}
    assert by_id["bot-1"].origin is MessageOrigin.BOT_DELIVERY
    assert by_id["bot-1"].decision_id == "d1"
    assert by_id["u1"].origin is MessageOrigin.PLATFORM_REALTIME
    assert store.latest_open_topic_epoch("g") is not None
    store.close()
    assert list(tmp_path.glob("legacy.db.pre-migrate-v6-to-v9.*"))


def test_newer_database_is_rejected(tmp_path):
    path = tmp_path / "future.db"
    db = sqlite3.connect(str(path))
    db.execute("CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("INSERT INTO schema_meta VALUES ('version', '99')")
    db.commit()
    db.close()
    with pytest.raises(SchemaTooNewError):
        SQLiteMemoryStore(path)


def test_failed_v7_migration_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / "broken.db"
    db = sqlite3.connect(str(path))
    with db:
        _bootstrap_v5(db)
        _v5_to_v6(db)
        db.execute("UPDATE schema_meta SET value='6' WHERE key='version'")
    db.close()

    def broken(db):
        db.execute("ALTER TABLE messages ADD COLUMN temporary_column TEXT")
        raise RuntimeError("boom")

    monkeypatch.setattr(migrations, "_v6_to_v7", broken)
    with pytest.raises(RuntimeError):
        migrate_database(path)

    db = sqlite3.connect(str(path))
    version = db.execute(
        "SELECT value FROM schema_meta WHERE key='version'"
    ).fetchone()[0]
    columns = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
    db.close()
    assert version == "6"
    assert "temporary_column" not in columns
    assert list(tmp_path.glob("broken.db.pre-migrate-v6-to-v9.*"))


def test_bot_delivery_without_decision_id_is_rejected(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "reject.db")
    with pytest.raises(ValueError):
        store.save_message(
            ChatMessage(
                message_id="bot-x",
                group_id="g",
                sender_id="__bot__",
                sender_name="爱弥斯",
                text="x",
                timestamp=1,
                is_bot=True,
                origin=MessageOrigin.BOT_DELIVERY,
            )
        )
    store.close()
