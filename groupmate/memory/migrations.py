"""Incremental, backup-first SQLite schema migrations."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional
from uuid import uuid4


SCHEMA_VERSION = 11


class SchemaMigrationError(RuntimeError):
    pass


class SchemaTooNewError(SchemaMigrationError):
    pass


class UnsupportedSchemaError(SchemaMigrationError):
    pass


def _version(db: sqlite3.Connection) -> int:
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_meta'"
    ).fetchone()
    if row is None:
        return 0
    value = db.execute(
        "SELECT value FROM schema_meta WHERE key='version'"
    ).fetchone()
    return int(value[0]) if value else 0


def _bootstrap_v5(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE messages (
            group_id TEXT NOT NULL, message_id TEXT NOT NULL,
            sender_id TEXT NOT NULL, sender_name TEXT NOT NULL,
            text TEXT NOT NULL, timestamp INTEGER NOT NULL,
            reply_to_message_id TEXT, reply_to_bot INTEGER NOT NULL,
            mentions_bot INTEGER NOT NULL, is_bot INTEGER NOT NULL,
            is_command INTEGER NOT NULL, image_urls TEXT NOT NULL,
            segment_types TEXT NOT NULL, metadata TEXT NOT NULL,
            PRIMARY KEY (group_id, message_id)
        );
        CREATE INDEX idx_messages_group_time
            ON messages(group_id, timestamp DESC);
        CREATE TABLE profiles (
            group_id TEXT NOT NULL, subject_id TEXT NOT NULL,
            display_name TEXT NOT NULL, relationship TEXT NOT NULL,
            authority INTEGER NOT NULL, updated_at INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (group_id, subject_id)
        );
        CREATE TABLE memories (
            memory_id TEXT PRIMARY KEY, group_id TEXT NOT NULL,
            subject_id TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
            created_at INTEGER NOT NULL, expires_at INTEGER,
            confidence REAL NOT NULL, importance REAL NOT NULL,
            authority INTEGER NOT NULL, source_message_id TEXT
        );
        CREATE INDEX idx_memories_group_subject
            ON memories(group_id, subject_id, created_at DESC);
        CREATE TABLE decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, decision_id TEXT NOT NULL,
            group_id TEXT NOT NULL, state TEXT NOT NULL, reason TEXT NOT NULL,
            timestamp INTEGER NOT NULL
        );
        CREATE INDEX idx_decisions_id ON decisions(decision_id, id);
        CREATE TABLE outbox (
            decision_id TEXT PRIMARY KEY, group_id TEXT NOT NULL,
            text TEXT NOT NULL, created_at INTEGER NOT NULL,
            expires_at INTEGER, sent_at INTEGER
        );
        CREATE TABLE favorability (
            group_id TEXT NOT NULL, user_id TEXT NOT NULL,
            score INTEGER NOT NULL, updated_at INTEGER NOT NULL,
            PRIMARY KEY (group_id, user_id)
        );
        INSERT INTO schema_meta(key, value) VALUES('version', '5');
        """
    )


def _v5_to_v6(db: sqlite3.Connection) -> None:
    additions = (
        ("status", "TEXT NOT NULL DEFAULT 'pending'"),
        ("attempt", "INTEGER NOT NULL DEFAULT 0"),
        ("failure_code", "TEXT"),
        ("failure_detail", "TEXT"),
        ("quote_message_id", "TEXT"),
        ("segments_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("kind", "TEXT NOT NULL DEFAULT 'reply'"),
    )
    existing = {
        str(row[1]) for row in db.execute("PRAGMA table_info(outbox)").fetchall()
    }
    for name, declaration in additions:
        if name not in existing:
            db.execute(
                "ALTER TABLE outbox ADD COLUMN {} {}".format(name, declaration)
            )
    db.execute("UPDATE outbox SET status='sent' WHERE sent_at IS NOT NULL")
    db.execute(
        "UPDATE outbox SET status='expired' "
        "WHERE sent_at IS NULL AND expires_at IS NOT NULL AND expires_at <= ?",
        (int(time.time()),),
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_outbox_status_created "
        "ON outbox(status, created_at)"
    )


def _v6_to_v7(db: sqlite3.Connection) -> None:
    additions = (
        ("origin", "TEXT NOT NULL DEFAULT 'PLATFORM_REALTIME'"),
        ("decision_id", "TEXT"),
        ("ingested_at", "INTEGER NOT NULL DEFAULT 0"),
        ("platform", "TEXT NOT NULL DEFAULT ''"),
        ("bot_id", "TEXT NOT NULL DEFAULT ''"),
        ("event_version", "INTEGER NOT NULL DEFAULT 1"),
    )
    existing = {
        str(row[1]) for row in db.execute("PRAGMA table_info(messages)").fetchall()
    }
    for name, declaration in additions:
        if name not in existing:
            db.execute(
                "ALTER TABLE messages ADD COLUMN {} {}".format(name, declaration)
            )

    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS topic_epochs (
            group_id TEXT NOT NULL,
            topic_id TEXT NOT NULL,
            opened_at INTEGER NOT NULL,
            closed_at INTEGER,
            close_reason TEXT,
            last_message_id TEXT,
            PRIMARY KEY (group_id, topic_id)
        );
        CREATE INDEX IF NOT EXISTS idx_topic_epochs_open
            ON topic_epochs(group_id, opened_at DESC);
        CREATE INDEX IF NOT EXISTS idx_topic_epochs_group_time
            ON topic_epochs(group_id, opened_at DESC);

        CREATE TABLE IF NOT EXISTS continuation_grants (
            grant_id TEXT NOT NULL PRIMARY KEY,
            group_id TEXT NOT NULL,
            sender_id TEXT NOT NULL,
            opened_by_decision_id TEXT NOT NULL,
            opened_by_message_id TEXT NOT NULL,
            trigger_kind TEXT NOT NULL,
            granted_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            max_total_seconds INTEGER NOT NULL,
            absolute_deadline_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_continuation_grants_lookup
            ON continuation_grants(group_id, sender_id, granted_at DESC);
        CREATE INDEX IF NOT EXISTS idx_continuation_grants_active
            ON continuation_grants(group_id, expires_at);
        """
    )

    rows = db.execute(
        "SELECT rowid, is_bot, metadata, timestamp, origin, decision_id, ingested_at "
        "FROM messages"
    ).fetchall()
    for row in rows:
        metadata = {}
        try:
            metadata = json.loads(row[2] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        meta_origin = str(metadata.get("origin") or "").strip().lower()
        meta_decision = metadata.get("decision_id")
        decision_id = row[5] or (str(meta_decision).strip() if meta_decision else None)
        is_bot = bool(row[1])
        if is_bot and (meta_origin == "bot_delivery" or decision_id):
            origin = "BOT_DELIVERY"
        elif is_bot:
            origin = "SYSTEM_SYNTHETIC"
            decision_id = None
        elif meta_origin == "platform_history":
            origin = "PLATFORM_HISTORY"
        else:
            origin = "PLATFORM_REALTIME"
            decision_id = None
        ingested_at = int(row[6] or 0)
        if ingested_at <= 0:
            ingested_at = int(row[3] or 0)
        db.execute(
            "UPDATE messages SET origin=?, decision_id=?, ingested_at=?, "
            "event_version=1 WHERE rowid=?",
            (origin, decision_id, ingested_at, row[0]),
        )

    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_group_ingest "
        "ON messages(group_id, timestamp ASC, ingested_at ASC)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_decision "
        "ON messages(decision_id)"
    )

    # Optional open epoch per group for bootstrap; no forged continuation grants.
    groups = db.execute(
        "SELECT group_id, MIN(timestamp), "
        "(SELECT message_id FROM messages m2 WHERE m2.group_id = m.group_id "
        " ORDER BY timestamp DESC, rowid DESC LIMIT 1) "
        "FROM messages m GROUP BY group_id"
    ).fetchall()
    for group_id, opened_at, last_message_id in groups:
        open_count = db.execute(
            "SELECT COUNT(*) FROM topic_epochs "
            "WHERE group_id=? AND closed_at IS NULL",
            (group_id,),
        ).fetchone()[0]
        if open_count:
            continue
        db.execute(
            "INSERT INTO topic_epochs("
            "group_id, topic_id, opened_at, closed_at, close_reason, last_message_id"
            ") VALUES (?, ?, ?, NULL, NULL, ?)",
            (group_id, uuid4().hex, int(opened_at or 0), last_message_id),
        )


def _v7_to_v8(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS social_events (
            event_id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            source_message_id TEXT NOT NULL,
            confidence REAL NOT NULL,
            occurred_at INTEGER NOT NULL,
            decision_id TEXT,
            UNIQUE(group_id, source_message_id, kind)
        );
        CREATE INDEX IF NOT EXISTS idx_social_events_group_user
            ON social_events(group_id, user_id, occurred_at DESC);
        CREATE INDEX IF NOT EXISTS idx_social_events_source
            ON social_events(group_id, source_message_id);

        CREATE TABLE IF NOT EXISTS relationship_state (
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            familiarity INTEGER NOT NULL DEFAULT 0,
            affinity INTEGER NOT NULL DEFAULT 0,
            trust INTEGER NOT NULL DEFAULT 0,
            boundary_pressure INTEGER NOT NULL DEFAULT 0,
            interaction_count INTEGER NOT NULL DEFAULT 0,
            last_interaction_at INTEGER NOT NULL DEFAULT 0,
            configured_relationship TEXT,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (group_id, user_id)
        );
        """
    )
    # Backfill affinity from legacy favorability; do not forge historical events.
    db.execute(
        """
        INSERT OR IGNORE INTO relationship_state(
            group_id, user_id, familiarity, affinity, trust, boundary_pressure,
            interaction_count, last_interaction_at, configured_relationship, updated_at
        )
        SELECT group_id, user_id, 0, score, 0, 0, 0, updated_at, NULL, updated_at
        FROM favorability
        """
    )


def _v8_to_v9(db: sqlite3.Connection) -> None:
    additions = (
        ("status", "TEXT NOT NULL DEFAULT 'accepted'"),
        ("scope", "TEXT NOT NULL DEFAULT 'USER_IN_GROUP'"),
        ("sensitivity", "TEXT NOT NULL DEFAULT 'none'"),
        ("extractor_version", "TEXT NOT NULL DEFAULT 'rules-v1'"),
        ("supersedes_memory_id", "TEXT"),
        ("source_message_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
    )
    existing = {
        str(row[1]) for row in db.execute("PRAGMA table_info(memories)").fetchall()
    }
    for name, declaration in additions:
        if name not in existing:
            db.execute(
                "ALTER TABLE memories ADD COLUMN {} {}".format(name, declaration)
            )

    rows = db.execute(
        "SELECT memory_id, source_message_id, source_message_ids_json FROM memories"
    ).fetchall()
    for memory_id, source_message_id, ids_json in rows:
        current = ids_json or "[]"
        try:
            parsed = json.loads(current)
        except (TypeError, ValueError):
            parsed = []
        if (not parsed) and source_message_id:
            db.execute(
                "UPDATE memories SET source_message_ids_json=? WHERE memory_id=?",
                (json.dumps([str(source_message_id)], ensure_ascii=False), memory_id),
            )

    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS memory_candidates (
            candidate_id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            scope TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            claim TEXT NOT NULL,
            claim_hash TEXT NOT NULL,
            source_message_ids_json TEXT NOT NULL,
            confidence REAL NOT NULL,
            sensitivity TEXT NOT NULL,
            proposed_expires_at INTEGER,
            extractor_version TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            decided_at INTEGER,
            decision_reason TEXT,
            UNIQUE(group_id, subject_id, claim_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_memory_candidates_group
            ON memory_candidates(group_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_memory_candidates_status
            ON memory_candidates(group_id, status);

        CREATE TABLE IF NOT EXISTS memory_tombstones (
            tombstone_id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            claim_hash TEXT NOT NULL,
            source_message_ids_json TEXT NOT NULL,
            deleted_at INTEGER NOT NULL,
            reason TEXT NOT NULL,
            UNIQUE(group_id, subject_id, claim_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_memory_tombstones_lookup
            ON memory_tombstones(group_id, subject_id, claim_hash);

        CREATE INDEX IF NOT EXISTS idx_memories_status
            ON memories(group_id, status, created_at DESC);
        """
    )


def _v9_to_v10(db: sqlite3.Connection) -> None:
    existing = {
        str(row[1]) for row in db.execute("PRAGMA table_info(outbox)").fetchall()
    }
    if "outbound_json" not in existing:
        db.execute(
            "ALTER TABLE outbox ADD COLUMN "
            "outbound_json TEXT NOT NULL DEFAULT '[]'"
        )


_V11_PERSONA_TABLES = (
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
)


def _v11_schema_sql() -> str:
    """Return the complete schema for a new persona-scoped database."""
    return """
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE messages (
            persona_id TEXT NOT NULL,
            group_id TEXT NOT NULL, message_id TEXT NOT NULL,
            sender_id TEXT NOT NULL, sender_name TEXT NOT NULL,
            text TEXT NOT NULL, timestamp INTEGER NOT NULL,
            reply_to_message_id TEXT, reply_to_bot INTEGER NOT NULL,
            mentions_bot INTEGER NOT NULL, is_bot INTEGER NOT NULL,
            is_command INTEGER NOT NULL, image_urls TEXT NOT NULL,
            segment_types TEXT NOT NULL, metadata TEXT NOT NULL,
            origin TEXT NOT NULL, decision_id TEXT,
            ingested_at INTEGER NOT NULL, platform TEXT NOT NULL,
            bot_id TEXT NOT NULL, event_version INTEGER NOT NULL,
            PRIMARY KEY (persona_id, group_id, message_id)
        );
        CREATE TABLE profiles (
            persona_id TEXT NOT NULL,
            group_id TEXT NOT NULL, subject_id TEXT NOT NULL,
            display_name TEXT NOT NULL, relationship TEXT NOT NULL,
            authority INTEGER NOT NULL, updated_at INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (persona_id, group_id, subject_id)
        );
        CREATE TABLE memories (
            memory_id TEXT PRIMARY KEY, persona_id TEXT NOT NULL,
            group_id TEXT NOT NULL, subject_id TEXT NOT NULL,
            kind TEXT NOT NULL, text TEXT NOT NULL, created_at INTEGER NOT NULL,
            expires_at INTEGER, confidence REAL NOT NULL, importance REAL NOT NULL,
            authority INTEGER NOT NULL, source_message_id TEXT,
            status TEXT NOT NULL DEFAULT 'accepted',
            scope TEXT NOT NULL DEFAULT 'USER_IN_GROUP',
            sensitivity TEXT NOT NULL DEFAULT 'none',
            extractor_version TEXT NOT NULL DEFAULT 'rules-v1',
            supersedes_memory_id TEXT,
            source_message_ids_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id TEXT NOT NULL, persona_id TEXT NOT NULL,
            group_id TEXT NOT NULL, state TEXT NOT NULL, reason TEXT NOT NULL,
            timestamp INTEGER NOT NULL
        );
        CREATE TABLE outbox (
            decision_id TEXT PRIMARY KEY, persona_id TEXT NOT NULL,
            group_id TEXT NOT NULL, text TEXT NOT NULL, created_at INTEGER NOT NULL,
            expires_at INTEGER, sent_at INTEGER,
            status TEXT NOT NULL DEFAULT 'pending', attempt INTEGER NOT NULL DEFAULT 0,
            failure_code TEXT, failure_detail TEXT, quote_message_id TEXT,
            segments_json TEXT NOT NULL DEFAULT '[]', kind TEXT NOT NULL DEFAULT 'reply',
            outbound_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE topic_epochs (
            persona_id TEXT NOT NULL,
            group_id TEXT NOT NULL, topic_id TEXT NOT NULL,
            opened_at INTEGER NOT NULL, closed_at INTEGER,
            close_reason TEXT, last_message_id TEXT,
            PRIMARY KEY (persona_id, group_id, topic_id)
        );
        CREATE TABLE continuation_grants (
            grant_id TEXT PRIMARY KEY, persona_id TEXT NOT NULL,
            group_id TEXT NOT NULL, sender_id TEXT NOT NULL,
            opened_by_decision_id TEXT NOT NULL, opened_by_message_id TEXT NOT NULL,
            trigger_kind TEXT NOT NULL, granted_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL, max_total_seconds INTEGER NOT NULL,
            absolute_deadline_at INTEGER NOT NULL
        );
        CREATE TABLE social_events (
            event_id TEXT PRIMARY KEY, persona_id TEXT NOT NULL,
            group_id TEXT NOT NULL, user_id TEXT NOT NULL, kind TEXT NOT NULL,
            source_message_id TEXT NOT NULL, confidence REAL NOT NULL,
            occurred_at INTEGER NOT NULL, decision_id TEXT,
            UNIQUE (persona_id, group_id, source_message_id, kind)
        );
        CREATE TABLE relationship_state (
            persona_id TEXT NOT NULL,
            group_id TEXT NOT NULL, user_id TEXT NOT NULL,
            familiarity INTEGER NOT NULL DEFAULT 0, affinity INTEGER NOT NULL DEFAULT 0,
            trust INTEGER NOT NULL DEFAULT 0, boundary_pressure INTEGER NOT NULL DEFAULT 0,
            interaction_count INTEGER NOT NULL DEFAULT 0,
            last_interaction_at INTEGER NOT NULL DEFAULT 0,
            configured_relationship TEXT, updated_at INTEGER NOT NULL,
            PRIMARY KEY (persona_id, group_id, user_id)
        );
        CREATE TABLE memory_candidates (
            candidate_id TEXT PRIMARY KEY, persona_id TEXT NOT NULL,
            group_id TEXT NOT NULL, scope TEXT NOT NULL, subject_id TEXT NOT NULL,
            kind TEXT NOT NULL, claim TEXT NOT NULL, claim_hash TEXT NOT NULL,
            source_message_ids_json TEXT NOT NULL, confidence REAL NOT NULL,
            sensitivity TEXT NOT NULL, proposed_expires_at INTEGER,
            extractor_version TEXT NOT NULL, status TEXT NOT NULL,
            created_at INTEGER NOT NULL, decided_at INTEGER, decision_reason TEXT,
            UNIQUE (persona_id, group_id, subject_id, claim_hash)
        );
        CREATE TABLE memory_tombstones (
            tombstone_id TEXT PRIMARY KEY, persona_id TEXT NOT NULL,
            group_id TEXT NOT NULL, subject_id TEXT NOT NULL, claim_hash TEXT NOT NULL,
            source_message_ids_json TEXT NOT NULL, deleted_at INTEGER NOT NULL,
            reason TEXT NOT NULL,
            UNIQUE (persona_id, group_id, subject_id, claim_hash)
        );
        INSERT INTO schema_meta(key, value) VALUES('version', '11');
    """


def _v11_indexes_sql() -> str:
    return """
        CREATE INDEX idx_messages_persona_group_time
            ON messages(persona_id, group_id, timestamp DESC);
        CREATE INDEX idx_messages_persona_group_ingest
            ON messages(persona_id, group_id, timestamp ASC, ingested_at ASC);
        CREATE INDEX idx_messages_persona_decision
            ON messages(persona_id, decision_id);
        CREATE INDEX idx_memories_persona_group_subject
            ON memories(persona_id, group_id, subject_id, created_at DESC);
        CREATE INDEX idx_memories_persona_status
            ON memories(persona_id, group_id, status, created_at DESC);
        CREATE INDEX idx_decisions_persona_id
            ON decisions(persona_id, decision_id, id);
        CREATE INDEX idx_outbox_persona_status_created
            ON outbox(persona_id, status, created_at);
        CREATE INDEX idx_topic_epochs_persona_group_open
            ON topic_epochs(persona_id, group_id, opened_at DESC);
        CREATE INDEX idx_continuation_persona_lookup
            ON continuation_grants(persona_id, group_id, sender_id, granted_at DESC);
        CREATE INDEX idx_continuation_persona_active
            ON continuation_grants(persona_id, group_id, expires_at);
        CREATE INDEX idx_social_persona_group_user
            ON social_events(persona_id, group_id, user_id, occurred_at DESC);
        CREATE INDEX idx_social_persona_source
            ON social_events(persona_id, group_id, source_message_id);
        CREATE INDEX idx_candidates_persona_group
            ON memory_candidates(persona_id, group_id, created_at DESC);
        CREATE INDEX idx_candidates_persona_status
            ON memory_candidates(persona_id, group_id, status);
        CREATE INDEX idx_tombstones_persona_lookup
            ON memory_tombstones(persona_id, group_id, subject_id, claim_hash);
    """


def _bootstrap_v11(db: sqlite3.Connection) -> None:
    db.executescript(_v11_schema_sql())
    db.executescript(_v11_indexes_sql())


_V11_COPY_COLUMNS = {
    "messages": (
        "group_id,message_id,sender_id,sender_name,text,timestamp,"
        "reply_to_message_id,reply_to_bot,mentions_bot,is_bot,is_command,"
        "image_urls,segment_types,metadata,origin,decision_id,ingested_at,"
        "platform,bot_id,event_version"
    ),
    "profiles": "group_id,subject_id,display_name,relationship,authority,updated_at",
    "memories": (
        "memory_id,group_id,subject_id,kind,text,created_at,expires_at,confidence,"
        "importance,authority,source_message_id,status,scope,sensitivity,"
        "extractor_version,supersedes_memory_id,source_message_ids_json"
    ),
    "decisions": "decision_id,group_id,state,reason,timestamp",
    "outbox": (
        "decision_id,group_id,text,created_at,expires_at,sent_at,status,attempt,"
        "failure_code,failure_detail,quote_message_id,segments_json,kind,outbound_json"
    ),
    "topic_epochs": "group_id,topic_id,opened_at,closed_at,close_reason,last_message_id",
    "continuation_grants": (
        "grant_id,group_id,sender_id,opened_by_decision_id,opened_by_message_id,"
        "trigger_kind,granted_at,expires_at,max_total_seconds,absolute_deadline_at"
    ),
    "social_events": (
        "event_id,group_id,user_id,kind,source_message_id,confidence,occurred_at,decision_id"
    ),
    "relationship_state": (
        "group_id,user_id,familiarity,affinity,trust,boundary_pressure,"
        "interaction_count,last_interaction_at,configured_relationship,updated_at"
    ),
    "memory_candidates": (
        "candidate_id,group_id,scope,subject_id,kind,claim,claim_hash,"
        "source_message_ids_json,confidence,sensitivity,proposed_expires_at,"
        "extractor_version,status,created_at,decided_at,decision_reason"
    ),
    "memory_tombstones": (
        "tombstone_id,group_id,subject_id,claim_hash,source_message_ids_json,"
        "deleted_at,reason"
    ),
}


def _rebuild_with_persona(
    db: sqlite3.Connection,
    *,
    table: str,
    create_sql: str,
    insert_columns: str,
) -> None:
    target = table + "_v11"
    db.execute(create_sql.format(table=target))
    db.execute(
        "INSERT INTO {target}(persona_id,{columns}) "
        "SELECT 'aemeath',{columns} FROM {table}".format(
            target=target,
            columns=insert_columns,
            table=table,
        )
    )
    old_count = db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
    new_count = db.execute("SELECT COUNT(*) FROM " + target).fetchone()[0]
    if old_count != new_count:
        raise SchemaMigrationError("row-count mismatch for " + table)
    db.execute("DROP TABLE " + table)
    db.execute("ALTER TABLE " + target + " RENAME TO " + table)


def _v11_table_create_sql(table: str) -> str:
    """Return one v11 CREATE TABLE statement with a caller-supplied name."""
    statements = {
        "messages": """
            CREATE TABLE {table} (
                persona_id TEXT NOT NULL, group_id TEXT NOT NULL, message_id TEXT NOT NULL,
                sender_id TEXT NOT NULL, sender_name TEXT NOT NULL, text TEXT NOT NULL,
                timestamp INTEGER NOT NULL, reply_to_message_id TEXT, reply_to_bot INTEGER NOT NULL,
                mentions_bot INTEGER NOT NULL, is_bot INTEGER NOT NULL, is_command INTEGER NOT NULL,
                image_urls TEXT NOT NULL, segment_types TEXT NOT NULL, metadata TEXT NOT NULL,
                origin TEXT NOT NULL, decision_id TEXT, ingested_at INTEGER NOT NULL,
                platform TEXT NOT NULL, bot_id TEXT NOT NULL, event_version INTEGER NOT NULL,
                PRIMARY KEY (persona_id, group_id, message_id)
            )
        """,
        "profiles": """
            CREATE TABLE {table} (
                persona_id TEXT NOT NULL, group_id TEXT NOT NULL, subject_id TEXT NOT NULL,
                display_name TEXT NOT NULL, relationship TEXT NOT NULL, authority INTEGER NOT NULL,
                updated_at INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (persona_id, group_id, subject_id)
            )
        """,
        "memories": """
            CREATE TABLE {table} (
                memory_id TEXT PRIMARY KEY, persona_id TEXT NOT NULL, group_id TEXT NOT NULL,
                subject_id TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
                created_at INTEGER NOT NULL, expires_at INTEGER, confidence REAL NOT NULL,
                importance REAL NOT NULL, authority INTEGER NOT NULL, source_message_id TEXT,
                status TEXT NOT NULL DEFAULT 'accepted', scope TEXT NOT NULL DEFAULT 'USER_IN_GROUP',
                sensitivity TEXT NOT NULL DEFAULT 'none', extractor_version TEXT NOT NULL DEFAULT 'rules-v1',
                supersedes_memory_id TEXT, source_message_ids_json TEXT NOT NULL DEFAULT '[]'
            )
        """,
        "decisions": """
            CREATE TABLE {table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT, decision_id TEXT NOT NULL,
                persona_id TEXT NOT NULL, group_id TEXT NOT NULL, state TEXT NOT NULL,
                reason TEXT NOT NULL, timestamp INTEGER NOT NULL
            )
        """,
        "outbox": """
            CREATE TABLE {table} (
                decision_id TEXT PRIMARY KEY, persona_id TEXT NOT NULL, group_id TEXT NOT NULL,
                text TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER, sent_at INTEGER,
                status TEXT NOT NULL DEFAULT 'pending', attempt INTEGER NOT NULL DEFAULT 0,
                failure_code TEXT, failure_detail TEXT, quote_message_id TEXT,
                segments_json TEXT NOT NULL DEFAULT '[]', kind TEXT NOT NULL DEFAULT 'reply',
                outbound_json TEXT NOT NULL DEFAULT '[]'
            )
        """,
        "topic_epochs": """
            CREATE TABLE {table} (
                persona_id TEXT NOT NULL, group_id TEXT NOT NULL, topic_id TEXT NOT NULL,
                opened_at INTEGER NOT NULL, closed_at INTEGER, close_reason TEXT,
                last_message_id TEXT, PRIMARY KEY (persona_id, group_id, topic_id)
            )
        """,
        "continuation_grants": """
            CREATE TABLE {table} (
                grant_id TEXT PRIMARY KEY, persona_id TEXT NOT NULL, group_id TEXT NOT NULL,
                sender_id TEXT NOT NULL, opened_by_decision_id TEXT NOT NULL,
                opened_by_message_id TEXT NOT NULL, trigger_kind TEXT NOT NULL,
                granted_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
                max_total_seconds INTEGER NOT NULL, absolute_deadline_at INTEGER NOT NULL
            )
        """,
        "social_events": """
            CREATE TABLE {table} (
                event_id TEXT PRIMARY KEY, persona_id TEXT NOT NULL, group_id TEXT NOT NULL,
                user_id TEXT NOT NULL, kind TEXT NOT NULL, source_message_id TEXT NOT NULL,
                confidence REAL NOT NULL, occurred_at INTEGER NOT NULL, decision_id TEXT,
                UNIQUE (persona_id, group_id, source_message_id, kind)
            )
        """,
        "relationship_state": """
            CREATE TABLE {table} (
                persona_id TEXT NOT NULL, group_id TEXT NOT NULL, user_id TEXT NOT NULL,
                familiarity INTEGER NOT NULL DEFAULT 0, affinity INTEGER NOT NULL DEFAULT 0,
                trust INTEGER NOT NULL DEFAULT 0, boundary_pressure INTEGER NOT NULL DEFAULT 0,
                interaction_count INTEGER NOT NULL DEFAULT 0, last_interaction_at INTEGER NOT NULL DEFAULT 0,
                configured_relationship TEXT, updated_at INTEGER NOT NULL,
                PRIMARY KEY (persona_id, group_id, user_id)
            )
        """,
        "memory_candidates": """
            CREATE TABLE {table} (
                candidate_id TEXT PRIMARY KEY, persona_id TEXT NOT NULL, group_id TEXT NOT NULL,
                scope TEXT NOT NULL, subject_id TEXT NOT NULL, kind TEXT NOT NULL, claim TEXT NOT NULL,
                claim_hash TEXT NOT NULL, source_message_ids_json TEXT NOT NULL, confidence REAL NOT NULL,
                sensitivity TEXT NOT NULL, proposed_expires_at INTEGER, extractor_version TEXT NOT NULL,
                status TEXT NOT NULL, created_at INTEGER NOT NULL, decided_at INTEGER,
                decision_reason TEXT, UNIQUE (persona_id, group_id, subject_id, claim_hash)
            )
        """,
        "memory_tombstones": """
            CREATE TABLE {table} (
                tombstone_id TEXT PRIMARY KEY, persona_id TEXT NOT NULL, group_id TEXT NOT NULL,
                subject_id TEXT NOT NULL, claim_hash TEXT NOT NULL, source_message_ids_json TEXT NOT NULL,
                deleted_at INTEGER NOT NULL, reason TEXT NOT NULL,
                UNIQUE (persona_id, group_id, subject_id, claim_hash)
            )
        """,
    }
    return statements[table]


def _v10_to_v11(db: sqlite3.Connection) -> None:
    for table in _V11_PERSONA_TABLES:
        _rebuild_with_persona(
            db,
            table=table,
            create_sql=_v11_table_create_sql(table),
            insert_columns=_V11_COPY_COLUMNS[table],
        )
    db.execute("DROP TABLE favorability")
    for statement in _v11_indexes_sql().split(";"):
        statement = statement.strip()
        if statement:
            db.execute(statement)


def _verify_v11(db: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    missing = set(_V11_PERSONA_TABLES) - tables
    if missing or "favorability" in tables:
        raise SchemaMigrationError("schema v11 table verification failed")
    for table in _V11_PERSONA_TABLES:
        info = {
            row[1]: row for row in db.execute("PRAGMA table_info(" + table + ")")
        }
        column = info.get("persona_id")
        if column is None or column[3] != 1 or column[4] is not None:
            raise SchemaMigrationError("schema v11 persona_id verification failed for " + table)
        if db.execute(
            "SELECT COUNT(*) FROM " + table + " WHERE persona_id IS NULL OR persona_id=''"
        ).fetchone()[0]:
            raise SchemaMigrationError("schema v11 contains empty persona_id in " + table)
    check = db.execute("PRAGMA integrity_check").fetchone()
    if not check or check[0] != "ok":
        raise SchemaMigrationError("schema v11 integrity check failed")


def _set_version(db: sqlite3.Connection, version: int) -> None:
    db.execute(
        "UPDATE schema_meta SET value=? WHERE key='version'",
        (str(version),),
    )


def migrate_database(path: Path) -> Optional[Path]:
    """Migrate an empty, v5, or v6 database and return the optional backup path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path))
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    current = _version(db)
    if current > SCHEMA_VERSION:
        db.close()
        raise SchemaTooNewError(
            "database schema {} is newer than supported {}".format(
                current, SCHEMA_VERSION
            )
        )
    if current not in (0, 5, 6, 7, 8, 9, 10, SCHEMA_VERSION):
        db.close()
        raise UnsupportedSchemaError(
            "no safe migration path from schema {}".format(current)
        )
    backup_path: Optional[Path] = None
    if current not in (0, SCHEMA_VERSION):
        backup_path = path.with_name(
            "{}.pre-migrate-v{}-to-v{}.{}".format(
                path.name, current, SCHEMA_VERSION, int(time.time())
            )
        )
        backup = sqlite3.connect(str(backup_path))
        try:
            db.backup(backup)
        finally:
            backup.close()
    try:
        if current == 0:
            with db:
                _bootstrap_v11(db)
            current = SCHEMA_VERSION
        if current == 5:
            db.execute("BEGIN IMMEDIATE")
            try:
                _v5_to_v6(db)
                columns = {
                    str(row[1])
                    for row in db.execute("PRAGMA table_info(outbox)").fetchall()
                }
                required = {"status", "attempt", "segments_json", "kind"}
                if not required.issubset(columns):
                    raise SchemaMigrationError("schema verification failed")
                _set_version(db, 6)
                db.commit()
            except BaseException:
                db.rollback()
                raise
            current = 6
        if current == 6:
            db.execute("BEGIN IMMEDIATE")
            try:
                _v6_to_v7(db)
                columns = {
                    str(row[1])
                    for row in db.execute("PRAGMA table_info(messages)").fetchall()
                }
                required = {
                    "origin",
                    "decision_id",
                    "ingested_at",
                    "platform",
                    "bot_id",
                    "event_version",
                }
                if not required.issubset(columns):
                    raise SchemaMigrationError("schema verification failed")
                tables = {
                    str(row[0])
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                if "topic_epochs" not in tables or "continuation_grants" not in tables:
                    raise SchemaMigrationError("schema verification failed")
                bad = db.execute(
                    "SELECT COUNT(*) FROM messages "
                    "WHERE origin='BOT_DELIVERY' AND "
                    "(decision_id IS NULL OR decision_id='')"
                ).fetchone()[0]
                if bad:
                    raise SchemaMigrationError(
                        "BOT_DELIVERY rows missing decision_id"
                    )
                _set_version(db, 7)
                db.commit()
            except BaseException:
                db.rollback()
                raise
            current = 7
        if current == 7:
            db.execute("BEGIN IMMEDIATE")
            try:
                _v7_to_v8(db)
                tables = {
                    str(row[0])
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                if "social_events" not in tables or "relationship_state" not in tables:
                    raise SchemaMigrationError("schema verification failed")
                columns = {
                    str(row[1])
                    for row in db.execute(
                        "PRAGMA table_info(relationship_state)"
                    ).fetchall()
                }
                required = {
                    "familiarity",
                    "affinity",
                    "trust",
                    "boundary_pressure",
                    "interaction_count",
                    "last_interaction_at",
                }
                if not required.issubset(columns):
                    raise SchemaMigrationError("schema verification failed")
                _set_version(db, 8)
                db.commit()
            except BaseException:
                db.rollback()
                raise
            current = 8
        if current == 8:
            db.execute("BEGIN IMMEDIATE")
            try:
                _v8_to_v9(db)
                tables = {
                    str(row[0])
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                if (
                    "memory_candidates" not in tables
                    or "memory_tombstones" not in tables
                ):
                    raise SchemaMigrationError("schema verification failed")
                columns = {
                    str(row[1])
                    for row in db.execute("PRAGMA table_info(memories)").fetchall()
                }
                required = {
                    "status",
                    "scope",
                    "sensitivity",
                    "extractor_version",
                    "source_message_ids_json",
                }
                if not required.issubset(columns):
                    raise SchemaMigrationError("schema verification failed")
                _set_version(db, 9)
                db.commit()
            except BaseException:
                db.rollback()
                raise
            current = 9
        if current == 9:
            db.execute("BEGIN IMMEDIATE")
            try:
                _v9_to_v10(db)
                columns = {
                    str(row[1])
                    for row in db.execute("PRAGMA table_info(outbox)").fetchall()
                }
                if "outbound_json" not in columns:
                    raise SchemaMigrationError("schema verification failed")
                _set_version(db, 10)
                db.commit()
            except BaseException:
                db.rollback()
                raise
            current = 10
        if current == 10:
            db.execute("BEGIN IMMEDIATE")
            try:
                _v10_to_v11(db)
                _verify_v11(db)
                _set_version(db, SCHEMA_VERSION)
                db.commit()
            except BaseException:
                db.rollback()
                raise
            current = SCHEMA_VERSION
        if _version(db) != SCHEMA_VERSION:
            raise SchemaMigrationError("schema verification failed")
    finally:
        db.close()
    return backup_path
