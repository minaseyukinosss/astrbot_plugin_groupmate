"""Schema v11 persona-scope bootstrap and migration coverage."""

from __future__ import annotations

import sqlite3

import pytest

import groupmate.memory.migrations as migrations
from groupmate.memory.migrations import (
    _bootstrap_v5,
    _v5_to_v6,
    _v6_to_v7,
    _v7_to_v8,
    _v8_to_v9,
    _v9_to_v10,
    migrate_database,
)


PERSONA_TABLES = {
    "messages",
    "profiles",
    "memories",
    "decisions",
    "outbox",
    "topic_epochs",
    "continuation_grants",
    "social_events",
    "relationship_state",
    "memory_candidates",
    "memory_tombstones",
}


def _columns(db: sqlite3.Connection, table: str):
    return {row[1]: row for row in db.execute(f"PRAGMA table_info({table})")}


def _build_v10_fixture(tmp_path):
    path = tmp_path / "legacy-v10.db"
    db = sqlite3.connect(str(path))
    with db:
        _bootstrap_v5(db)
        _v5_to_v6(db)
        _v6_to_v7(db)
        _v7_to_v8(db)
        _v8_to_v9(db)
        _v9_to_v10(db)
        db.execute("UPDATE schema_meta SET value='10' WHERE key='version'")
        db.execute(
            "INSERT INTO messages(group_id,message_id,sender_id,sender_name,text,"
            "timestamp,reply_to_message_id,reply_to_bot,mentions_bot,is_bot,"
            "is_command,image_urls,segment_types,metadata,origin,decision_id,"
            "ingested_at,platform,bot_id,event_version) VALUES "
            "('g','msg','u','User','hi',1,NULL,0,0,0,0,'[]','[\"text\"]',"
            "'{}','PLATFORM_REALTIME',NULL,1,'onebot','bot',1)"
        )
        db.execute(
            "INSERT INTO profiles(group_id,subject_id,display_name,relationship,"
            "authority,updated_at) VALUES ('g','u','User','普通群友',1,1)"
        )
        db.execute(
            "INSERT INTO memories(memory_id,group_id,subject_id,kind,text,created_at,"
            "expires_at,confidence,importance,authority,source_message_id,status,"
            "scope,sensitivity,extractor_version,supersedes_memory_id,"
            "source_message_ids_json) VALUES "
            "('mem','g','u','semantic','likes tea',1,NULL,0.9,0.5,1,'msg',"
            "'accepted','USER_IN_GROUP','none','rules-v1',NULL,'[\"msg\"]')"
        )
        db.execute(
            "INSERT INTO decisions(decision_id,group_id,state,reason,timestamp) "
            "VALUES ('decision','g','END','sent',1)"
        )
        db.execute(
            "INSERT INTO outbox(decision_id,group_id,text,created_at,status,"
            "segments_json,kind,outbound_json) VALUES "
            "('decision','g','ok',1,'sent','[\"ok\"]','reply','[]')"
        )
        db.execute(
            "INSERT INTO topic_epochs(group_id,topic_id,opened_at,last_message_id) "
            "VALUES ('g','topic',1,'msg')"
        )
        db.execute(
            "INSERT INTO continuation_grants(grant_id,group_id,sender_id,"
            "opened_by_decision_id,opened_by_message_id,trigger_kind,granted_at,"
            "expires_at,max_total_seconds,absolute_deadline_at) VALUES "
            "('grant','g','u','decision','msg','alias_direct',1,10,90,91)"
        )
        db.execute(
            "INSERT INTO social_events(event_id,group_id,user_id,kind,"
            "source_message_id,confidence,occurred_at,decision_id) VALUES "
            "('event','g','u','positive','msg',0.9,1,'decision')"
        )
        db.execute(
            "INSERT OR REPLACE INTO relationship_state(group_id,user_id,"
            "familiarity,affinity,trust,boundary_pressure,interaction_count,"
            "last_interaction_at,configured_relationship,updated_at) VALUES "
            "('g','u',1,50,1,0,1,1,'闺蜜',1)"
        )
        db.execute(
            "INSERT INTO memory_candidates(candidate_id,group_id,scope,subject_id,"
            "kind,claim,claim_hash,source_message_ids_json,confidence,sensitivity,"
            "proposed_expires_at,extractor_version,status,created_at) VALUES "
            "('candidate','g','USER_IN_GROUP','u','semantic','likes tea','hash',"
            "'[\"msg\"]',0.9,'none',NULL,'rules-v1','pending',1)"
        )
        db.execute(
            "INSERT INTO memory_tombstones(tombstone_id,group_id,subject_id,"
            "claim_hash,source_message_ids_json,deleted_at,reason) VALUES "
            "('tomb','g','u','old-hash','[\"msg\"]',1,'test')"
        )
        db.execute(
            "INSERT INTO favorability(group_id,user_id,score,updated_at) "
            "VALUES ('g','u',50,1)"
        )
    db.close()
    return path


def _table_counts(path):
    db = sqlite3.connect(str(path))
    try:
        return {
            table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in PERSONA_TABLES
        }
    finally:
        db.close()


def test_empty_database_bootstraps_directly_to_v11(tmp_path):
    path = tmp_path / "new.db"
    migrate_database(path)
    db = sqlite3.connect(str(path))
    try:
        assert db.execute(
            "SELECT value FROM schema_meta WHERE key='version'"
        ).fetchone()[0] == "11"
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "favorability" not in tables
        for table in PERSONA_TABLES:
            column = _columns(db, table)["persona_id"]
            assert column[3] == 1
            assert column[4] is None
    finally:
        db.close()


def test_v10_rows_are_backfilled_to_aemeath_and_backup_is_created(tmp_path):
    path = _build_v10_fixture(tmp_path)
    before = _table_counts(path)
    migrate_database(path)
    db = sqlite3.connect(str(path))
    try:
        for table, count in before.items():
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == count
            assert db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE persona_id='aemeath'"
            ).fetchone()[0] == count
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "favorability" not in tables
    finally:
        db.close()
    assert list(tmp_path.glob("*.pre-migrate-v10-to-v11.*"))


def test_v11_persona_columns_reject_missing_values(tmp_path):
    path = tmp_path / "new.db"
    migrate_database(path)
    db = sqlite3.connect(str(path))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO profiles(group_id,subject_id,display_name,relationship,"
                "authority,updated_at) VALUES ('g','u','User','普通群友',1,1)"
            )
    finally:
        db.close()


def test_failed_v11_verification_rolls_back_v10_database(tmp_path, monkeypatch):
    path = _build_v10_fixture(tmp_path)

    def fail_verification(_db):
        raise RuntimeError("boom")

    monkeypatch.setattr(migrations, "_verify_v11", fail_verification, raising=False)
    with pytest.raises(RuntimeError, match="boom"):
        migrate_database(path)

    db = sqlite3.connect(str(path))
    try:
        assert db.execute(
            "SELECT value FROM schema_meta WHERE key='version'"
        ).fetchone()[0] == "10"
        assert "persona_id" not in _columns(db, "relationship_state")
    finally:
        db.close()
