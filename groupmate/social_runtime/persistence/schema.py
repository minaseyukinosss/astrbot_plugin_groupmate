"""Clean database bootstrap and verification for Social Runtime v2."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path


SCHEMA_VERSION = 2


class ForeignDatabaseError(RuntimeError):
    """Raised before a legacy or unrelated database can be mutated."""


class SchemaVerificationError(RuntimeError):
    """Raised when a Social Runtime database is incomplete or incompatible."""


_REQUIRED_TABLES = {
    "social_runtime_schema", "inbox", "journal", "actor_cursors", "snapshots",
    "persona_state", "persona_effects", "group_world", "scene_work_requests",
    "attention_frames",
    "cognitive_observations",
    "candidate_intentions", "governor_results", "action_plans", "tasks",
    "task_events", "delivery_bundles", "outbox", "relationship_events",
    "relationship_projection", "impressions", "culture", "memories",
    "memory_tombstones", "config_versions", "governance_actions",
    "projection_cursors", "evaluation_labels",
    "member_identities", "member_aliases", "profile_observations",
    "profile_facts", "profile_episodes", "social_edges",
    "profile_snapshots", "group_portraits", "profile_preferences",
    "profile_audit",
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
            row = db.execute(
                "SELECT version FROM social_runtime_schema WHERE singleton=1"
            ).fetchone()
            version = int(row[0]) if row is not None else 0
            if version == 1:
                _migrate_v1_to_v2(db)
            verify_schema(db)
            return
        db.executescript(_SCHEMA_SQL + _PROFILE_SCHEMA_SQL)
        db.execute(
            "INSERT INTO social_runtime_schema(singleton, version, created_at) "
            "VALUES(1, ?, ?)",
            (SCHEMA_VERSION, int(time.time())),
        )
        verify_schema(db)


def _migrate_v1_to_v2(db: sqlite3.Connection) -> None:
    """Upgrade an owned v1 database without rebuilding runtime tables."""

    db.executescript(
        "BEGIN IMMEDIATE;\n"
        + _PROFILE_SCHEMA_SQL
        + "\nUPDATE social_runtime_schema SET version=2 WHERE singleton=1;\n"
        + "COMMIT;"
    )


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
CREATE TABLE persona_effects (
    effect_id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    expected_version INTEGER NOT NULL,
    effect_json TEXT NOT NULL,
    result_state_json TEXT NOT NULL,
    applied_version INTEGER NOT NULL,
    applied_at INTEGER NOT NULL
);
CREATE INDEX idx_persona_effects_persona_version
    ON persona_effects(persona_id, applied_version);
CREATE TABLE group_world (
    persona_id TEXT NOT NULL, group_id TEXT NOT NULL, version INTEGER NOT NULL,
    state_json TEXT NOT NULL, updated_at INTEGER NOT NULL,
    PRIMARY KEY(persona_id, group_id)
);
CREATE TABLE scene_work_requests (
    request_id TEXT PRIMARY KEY,
    actor_key TEXT NOT NULL,
    trigger_event_id TEXT NOT NULL,
    scene_version INTEGER NOT NULL,
    request_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','accepted','stale')),
    resolution_json TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX idx_scene_work_pending
    ON scene_work_requests(actor_key, status, scene_version);
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


_PROFILE_SCHEMA_SQL = """
CREATE TABLE member_identities (
    persona_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    avatar_ref TEXT,
    system_roles_json TEXT NOT NULL,
    first_seen_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(persona_id, platform, actor_id)
);
CREATE TABLE member_aliases (
    persona_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    alias_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    source_event_id TEXT,
    status TEXT NOT NULL,
    first_seen_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    PRIMARY KEY(persona_id, group_id, actor_id, alias)
);
CREATE INDEX idx_member_aliases_scope
    ON member_aliases(persona_id, group_id, actor_id, status);
CREATE TABLE profile_observations (
    event_id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    observation_json TEXT NOT NULL,
    occurred_at INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN
      ('pending','processing','retry','completed','discarded')),
    attempt INTEGER NOT NULL DEFAULT 0,
    next_attempt_at INTEGER NOT NULL DEFAULT 0,
    diagnostic_code TEXT
);
CREATE INDEX idx_profile_observations_due
    ON profile_observations(persona_id, group_id, status, next_attempt_at, occurred_at);
CREATE TABLE profile_facts (
    fact_id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    category TEXT NOT NULL,
    summary TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_actor_id TEXT NOT NULL,
    source_event_ids_json TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    evidence_count INTEGER NOT NULL,
    valid_from INTEGER NOT NULL,
    valid_until INTEGER,
    supersedes_fact_id TEXT,
    injectable INTEGER NOT NULL CHECK(injectable IN (0,1)),
    updated_at INTEGER NOT NULL
);
CREATE INDEX idx_profile_facts_scope
    ON profile_facts(persona_id, group_id, subject_id, status, injectable);
CREATE TABLE profile_episodes (
    episode_id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    participants_json TEXT NOT NULL,
    source_event_ids_json TEXT NOT NULL,
    episode_type TEXT NOT NULL,
    valence REAL NOT NULL,
    importance REAL NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    occurred_at INTEGER NOT NULL,
    last_reinforced_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX idx_profile_episodes_scope
    ON profile_episodes(persona_id, group_id, status, occurred_at);
CREATE TABLE social_edges (
    edge_id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    source_member_id TEXT NOT NULL,
    target_member_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    strength REAL NOT NULL,
    confidence REAL NOT NULL,
    source_event_ids_json TEXT NOT NULL,
    status TEXT NOT NULL,
    valid_from INTEGER NOT NULL,
    valid_until INTEGER,
    last_observed_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX idx_social_edges_scope
    ON social_edges(persona_id, group_id, source_member_id, target_member_id, status);
CREATE TABLE profile_snapshots (
    persona_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    source_revision INTEGER NOT NULL,
    generated_at INTEGER NOT NULL,
    PRIMARY KEY(persona_id, group_id, subject_id)
);
CREATE TABLE group_portraits (
    persona_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    portrait_json TEXT NOT NULL,
    source_revision INTEGER NOT NULL,
    generated_at INTEGER NOT NULL,
    PRIMARY KEY(persona_id, group_id)
);
CREATE TABLE profile_preferences (
    persona_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    personalization_enabled INTEGER NOT NULL CHECK(personalization_enabled IN (0,1)),
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(persona_id, group_id, subject_id)
);
CREATE TABLE profile_audit (
    audit_id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    subject_id TEXT,
    actor_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target_id TEXT,
    audit_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX idx_profile_audit_scope
    ON profile_audit(persona_id, group_id, subject_id, created_at);
"""
