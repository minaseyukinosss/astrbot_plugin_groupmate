"""Clean database bootstrap and verification for Social Runtime v2."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path


SCHEMA_VERSION = 1


class ForeignDatabaseError(RuntimeError):
    """Raised before a legacy or unrelated database can be mutated."""


class SchemaVerificationError(RuntimeError):
    """Raised when a Social Runtime database is incomplete or incompatible."""


_REQUIRED_TABLES = {
    "social_runtime_schema", "inbox", "journal", "actor_cursors", "snapshots",
    "persona_state", "group_world", "attention_frames", "cognitive_observations",
    "candidate_intentions", "governor_results", "action_plans", "tasks",
    "task_events", "delivery_bundles", "outbox", "relationship_events",
    "relationship_projection", "impressions", "culture", "memories",
    "memory_tombstones", "config_versions", "governance_actions",
    "projection_cursors", "evaluation_labels",
}


def connect_database(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(Path(path)))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    return db


def initialize_database(path: Path) -> None:
    path = Path(path)
    if path.name == "groupmate.db":
        raise ForeignDatabaseError("legacy database path is not accepted")

    existed_with_data = path.exists() and path.stat().st_size > 0
    if existed_with_data:
        with sqlite3.connect(str(path)) as probe:
            owned = probe.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='social_runtime_schema'"
            ).fetchone()
        if owned is None:
            raise ForeignDatabaseError("not a Social Runtime database")

    path.parent.mkdir(parents=True, exist_ok=True)
    with connect_database(path) as db:
        if existed_with_data:
            verify_schema(db)
            return
        db.executescript(_SCHEMA_SQL)
        db.execute(
            "INSERT INTO social_runtime_schema(singleton, version, created_at) "
            "VALUES(1, ?, ?)",
            (SCHEMA_VERSION, int(time.time())),
        )
        verify_schema(db)


def verify_schema(db: sqlite3.Connection) -> None:
    names = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing = sorted(_REQUIRED_TABLES - names)
    if missing:
        raise SchemaVerificationError(
            "Social Runtime schema is missing tables: {}".format(", ".join(missing))
        )
    row = db.execute(
        "SELECT version FROM social_runtime_schema WHERE singleton=1"
    ).fetchone()
    if row is None or int(row[0]) != SCHEMA_VERSION:
        raise SchemaVerificationError("unsupported Social Runtime schema version")
    integrity = db.execute("PRAGMA integrity_check").fetchone()
    if integrity is None or integrity[0] != "ok":
        raise SchemaVerificationError("Social Runtime database integrity check failed")


_SCHEMA_SQL = """
CREATE TABLE social_runtime_schema (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    version INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE inbox (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    persona_id TEXT NOT NULL,
    group_id TEXT,
    envelope_json TEXT NOT NULL,
    received_at INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','processing','committed','failed')),
    attempt INTEGER NOT NULL DEFAULT 0,
    failure_code TEXT,
    claimed_by TEXT
);
CREATE INDEX idx_inbox_status_sequence ON inbox(status, sequence);
CREATE TABLE journal (
    effect_id TEXT PRIMARY KEY,
    source_event_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    causation_id TEXT,
    actor_key TEXT NOT NULL,
    effect_type TEXT NOT NULL,
    effect_json TEXT NOT NULL,
    committed_at INTEGER NOT NULL
);
CREATE INDEX idx_journal_correlation ON journal(correlation_id, committed_at);
CREATE TABLE actor_cursors (
    actor_key TEXT PRIMARY KEY,
    last_sequence INTEGER NOT NULL,
    version INTEGER NOT NULL
);
CREATE TABLE snapshots (
    actor_key TEXT NOT NULL,
    version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(actor_key, version)
);
CREATE TABLE persona_state (
    persona_id TEXT PRIMARY KEY, version INTEGER NOT NULL,
    state_json TEXT NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE group_world (
    persona_id TEXT NOT NULL, group_id TEXT NOT NULL, version INTEGER NOT NULL,
    state_json TEXT NOT NULL, updated_at INTEGER NOT NULL,
    PRIMARY KEY(persona_id, group_id)
);
CREATE TABLE attention_frames (
    frame_id TEXT PRIMARY KEY, persona_id TEXT NOT NULL, group_id TEXT NOT NULL,
    scene_version INTEGER NOT NULL, status TEXT NOT NULL, frame_json TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE TABLE cognitive_observations (
    observation_id TEXT PRIMARY KEY, frame_id TEXT NOT NULL,
    persona_id TEXT NOT NULL, group_id TEXT NOT NULL, scene_version INTEGER NOT NULL,
    observation_json TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE candidate_intentions (
    intention_id TEXT PRIMARY KEY, frame_id TEXT NOT NULL,
    persona_id TEXT NOT NULL, group_id TEXT NOT NULL, scene_version INTEGER NOT NULL,
    intention_json TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE governor_results (
    result_id TEXT PRIMARY KEY, frame_id TEXT NOT NULL,
    persona_id TEXT NOT NULL, group_id TEXT NOT NULL, scene_version INTEGER NOT NULL,
    result_json TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE action_plans (
    plan_id TEXT PRIMARY KEY, correlation_id TEXT NOT NULL,
    persona_id TEXT NOT NULL, group_id TEXT NOT NULL, scene_version INTEGER NOT NULL,
    status TEXT NOT NULL, plan_json TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY, correlation_id TEXT NOT NULL,
    persona_id TEXT NOT NULL, group_id TEXT NOT NULL, status TEXT NOT NULL,
    task_json TEXT NOT NULL, version INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE task_events (
    event_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
    event_type TEXT NOT NULL, event_json TEXT NOT NULL, occurred_at INTEGER NOT NULL
);
CREATE TABLE delivery_bundles (
    bundle_id TEXT PRIMARY KEY, correlation_id TEXT NOT NULL,
    persona_id TEXT NOT NULL, group_id TEXT NOT NULL, status TEXT NOT NULL,
    bundle_json TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE outbox (
    part_id TEXT PRIMARY KEY,
    bundle_id TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN
      ('planned','ready','sending','sent','failed','unknown','expired','suppressed')),
    payload_json TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    receipt_json TEXT
);
CREATE INDEX idx_outbox_status_expiry ON outbox(status, expires_at);
CREATE TABLE relationship_events (
    event_id TEXT PRIMARY KEY, persona_id TEXT NOT NULL, group_id TEXT NOT NULL,
    subject_id TEXT NOT NULL, event_json TEXT NOT NULL, occurred_at INTEGER NOT NULL
);
CREATE TABLE relationship_projection (
    persona_id TEXT NOT NULL, group_id TEXT NOT NULL, subject_id TEXT NOT NULL,
    version INTEGER NOT NULL, projection_json TEXT NOT NULL, updated_at INTEGER NOT NULL,
    PRIMARY KEY(persona_id, group_id, subject_id)
);
CREATE TABLE impressions (
    impression_id TEXT PRIMARY KEY, persona_id TEXT NOT NULL, group_id TEXT NOT NULL,
    subject_id TEXT NOT NULL, status TEXT NOT NULL, impression_json TEXT NOT NULL,
    expires_at INTEGER
);
CREATE TABLE culture (
    artifact_id TEXT PRIMARY KEY, persona_id TEXT NOT NULL, group_id TEXT NOT NULL,
    status TEXT NOT NULL, artifact_json TEXT NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE memories (
    memory_id TEXT PRIMARY KEY, persona_id TEXT NOT NULL, group_id TEXT NOT NULL,
    subject_id TEXT, kind TEXT NOT NULL, sensitivity TEXT NOT NULL,
    memory_json TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER
);
CREATE TABLE memory_tombstones (
    tombstone_id TEXT PRIMARY KEY, persona_id TEXT NOT NULL, group_id TEXT NOT NULL,
    subject_id TEXT, content_hash TEXT NOT NULL, created_at INTEGER NOT NULL,
    UNIQUE(persona_id, group_id, content_hash)
);
CREATE TABLE config_versions (
    config_id TEXT NOT NULL, version INTEGER NOT NULL, persona_id TEXT NOT NULL,
    group_id TEXT, status TEXT NOT NULL, config_json TEXT NOT NULL,
    created_at INTEGER NOT NULL, PRIMARY KEY(config_id, version)
);
CREATE TABLE governance_actions (
    action_id TEXT PRIMARY KEY, command_id TEXT NOT NULL UNIQUE,
    persona_id TEXT NOT NULL, group_id TEXT, actor_id TEXT NOT NULL,
    action_type TEXT NOT NULL, reason TEXT NOT NULL, action_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE projection_cursors (
    projection_name TEXT PRIMARY KEY, last_journal_rowid INTEGER NOT NULL,
    version INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE evaluation_labels (
    label_id TEXT PRIMARY KEY, persona_id TEXT NOT NULL, group_id TEXT NOT NULL,
    source_event_id TEXT NOT NULL, label_json TEXT NOT NULL, created_at INTEGER NOT NULL
);
"""
