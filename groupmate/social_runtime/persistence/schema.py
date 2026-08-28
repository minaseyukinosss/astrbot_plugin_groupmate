"""Clean database bootstrap and verification for Social Runtime v2."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path


SCHEMA_VERSION = 5


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
    "member_style_settings", "member_speech_style_versions",
    "imitation_sessions",
    "knowledge_seeds", "knowledge_observations", "knowledge_entities",
    "knowledge_aliases", "group_knowledge_aliases", "knowledge_claims",
    "knowledge_sources", "knowledge_claim_evidence", "group_conventions",
    "group_topic_affinity", "group_topic_mentions", "game_release_states",
    "negative_search_snapshots", "knowledge_jobs", "knowledge_usage",
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
            user_objects = {
                str(row[0])
                for row in probe.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
            owned = "social_runtime_schema" in user_objects
        if not owned:
            if user_objects:
                raise ForeignDatabaseError("not a Social Runtime database")
            # A stale read can recreate an empty SQLite shell immediately
            # after operators delete the old runtime database. With no user
            # schema objects there is nothing foreign to protect, so treat it
            # exactly like a new file and bootstrap the authoritative schema.
            existed_with_data = False

    path.parent.mkdir(parents=True, exist_ok=True)
    with connect_database(path) as db:
        if existed_with_data:
            row = db.execute(
                "SELECT version FROM social_runtime_schema WHERE singleton=1"
            ).fetchone()
            version = int(row[0]) if row is not None else 0
            if version == 1:
                _migrate_v1_to_v2(db)
                version = 2
            if version == 2:
                _migrate_v2_to_v3(db)
                version = 3
            if version == 3:
                _migrate_v3_to_v4(db)
                version = 4
            if version == 4:
                _migrate_v4_to_v5(db)
            verify_schema(db)
            return
        db.executescript(
            _SCHEMA_SQL
            + _PROFILE_SCHEMA_SQL
            + _MEMBER_STYLE_SCHEMA_SQL
            + _KNOWLEDGE_SCHEMA_SQL
        )
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


def _migrate_v2_to_v3(db: sqlite3.Connection) -> None:
    """Add style assets without changing existing profile or runtime rows."""

    db.executescript(
        "BEGIN IMMEDIATE;\n"
        + _MEMBER_STYLE_SCHEMA_SQL
        + "\nUPDATE social_runtime_schema SET version=3 WHERE singleton=1;\n"
        + "COMMIT;"
    )


def _migrate_v3_to_v4(db: sqlite3.Connection) -> None:
    """Add persona-independent knowledge tables without rebuilding v3 data."""

    db.executescript(
        "BEGIN IMMEDIATE;\n"
        + _KNOWLEDGE_SCHEMA_SQL
        + "\nUPDATE social_runtime_schema SET version=4 WHERE singleton=1;\n"
        + "COMMIT;"
    )


def _migrate_v4_to_v5(db: sqlite3.Connection) -> None:
    """Add independent release evidence time without rewriting v4 rows."""

    columns = {
        str(row[1])
        for row in db.execute("PRAGMA table_info(game_release_states)")
    }
    db.execute("BEGIN IMMEDIATE")
    if "release_checked_at" not in columns:
        db.execute(
            "ALTER TABLE game_release_states ADD COLUMN release_checked_at INTEGER"
        )
    db.execute("UPDATE social_runtime_schema SET version=5 WHERE singleton=1")
    db.execute("COMMIT")


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
    release_columns = {
        str(row[1])
        for row in db.execute("PRAGMA table_info(game_release_states)")
    }
    if "release_checked_at" not in release_columns:
        raise SchemaVerificationError(
            "game_release_states is missing release_checked_at"
        )


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


_MEMBER_STYLE_SCHEMA_SQL = """
CREATE TABLE member_style_settings (
    group_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    enabled_at INTEGER NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    version INTEGER NOT NULL,
    collection_windows_json TEXT NOT NULL,
    PRIMARY KEY(group_id, member_id)
);
CREATE INDEX idx_member_style_settings_enabled
    ON member_style_settings(enabled, group_id, member_id);
CREATE TABLE member_speech_style_versions (
    group_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('READY','FAILED')),
    style_json TEXT NOT NULL,
    eligible_message_count INTEGER NOT NULL,
    active_day_count INTEGER NOT NULL,
    scene_types_json TEXT NOT NULL,
    evidence_event_ids_json TEXT NOT NULL,
    generated_at INTEGER NOT NULL,
    PRIMARY KEY(group_id, member_id, version)
);
CREATE INDEX idx_member_speech_style_ready
    ON member_speech_style_versions(group_id, member_id, status, version);
CREATE TABLE imitation_sessions (
    session_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    target_member_id TEXT NOT NULL,
    target_display_name TEXT NOT NULL,
    style_version INTEGER NOT NULL,
    started_by_admin_id TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    stopped_at INTEGER,
    stopped_by TEXT,
    stop_reason TEXT,
    FOREIGN KEY(group_id, target_member_id, style_version)
        REFERENCES member_speech_style_versions(group_id, member_id, version)
);
CREATE INDEX idx_imitation_sessions_active
    ON imitation_sessions(group_id, stopped_at, expires_at, started_at);
"""


_KNOWLEDGE_SCHEMA_SQL = """
CREATE TABLE knowledge_seeds (
    seed_id TEXT NOT NULL,
    seed_version INTEGER NOT NULL CHECK(seed_version > 0),
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','superseded','rejected')),
    manifest_json TEXT NOT NULL,
    imported_at INTEGER NOT NULL,
    PRIMARY KEY(seed_id, seed_version),
    UNIQUE(seed_id, content_hash)
);
CREATE TABLE knowledge_observations (
    observation_id TEXT PRIMARY KEY,
    origin_class TEXT NOT NULL CHECK(origin_class IN
      ('seed','human_chat','own_output','external_bot','unknown_actor','command',
       'forward','official_page','search_result','admin')),
    scope_kind TEXT NOT NULL CHECK(scope_kind IN ('global','group')),
    group_id TEXT,
    author_ref TEXT,
    source_event_id TEXT,
    source_id TEXT,
    entity_hint TEXT,
    safe_summary TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    occurred_at INTEGER NOT NULL,
    recorded_at INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','admitted','rejected','expired')),
    CHECK(
      (scope_kind='global' AND group_id IS NULL)
      OR (scope_kind='group' AND group_id IS NOT NULL)
    ),
    CHECK(
      author_ref IS NULL
      OR (scope_kind='group' AND origin_class='human_chat')
    )
);
CREATE UNIQUE INDEX idx_knowledge_observation_scoped_hash
    ON knowledge_observations(scope_kind, COALESCE(group_id, ''), content_hash);
CREATE INDEX idx_knowledge_observation_status
    ON knowledge_observations(status, recorded_at, observation_id);
CREATE INDEX idx_knowledge_observation_group
    ON knowledge_observations(group_id, occurred_at, observation_id);
CREATE TABLE knowledge_entities (
    entity_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    canonical_game_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN
      ('candidate','active','stale','superseded','rejected')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX idx_knowledge_entities_game
    ON knowledge_entities(canonical_game_id, entity_type, status, canonical_name);
CREATE TABLE knowledge_aliases (
    alias_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES knowledge_entities(entity_id),
    normalized_alias TEXT NOT NULL,
    alias_kind TEXT NOT NULL CHECK(alias_kind IN
      ('official','translation','abbreviation','community')),
    ambiguity_level TEXT NOT NULL CHECK(ambiguity_level IN
      ('none','contextual','high')),
    source_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('candidate','active','stale','rejected')),
    UNIQUE(entity_id, normalized_alias, alias_kind)
);
CREATE INDEX idx_knowledge_alias_lookup
    ON knowledge_aliases(normalized_alias, status, ambiguity_level);
CREATE TABLE group_knowledge_aliases (
    alias_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    entity_id TEXT NOT NULL REFERENCES knowledge_entities(entity_id),
    normalized_alias TEXT NOT NULL,
    evidence_observation_ids_json TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    last_used_at INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN
      ('candidate','active','stale','rejected','disputed')),
    UNIQUE(group_id, normalized_alias, entity_id)
);
CREATE INDEX idx_group_knowledge_alias_lookup
    ON group_knowledge_aliases(group_id, normalized_alias, status);
CREATE TABLE knowledge_claims (
    claim_id TEXT PRIMARY KEY,
    subject_entity_id TEXT NOT NULL REFERENCES knowledge_entities(entity_id),
    predicate TEXT NOT NULL,
    safe_summary TEXT NOT NULL,
    claim_kind TEXT NOT NULL CHECK(claim_kind IN
      ('stable_semantic','public_fact','rumor')),
    evidence_level TEXT NOT NULL CHECK(evidence_level IN
      ('bundled','official','corroborated','secondary','unofficial')),
    status TEXT NOT NULL CHECK(status IN
      ('pending','active','stale','superseded','rejected','disputed')),
    applies_to_version_slot_id TEXT,
    region TEXT,
    platform TEXT,
    valid_from INTEGER,
    valid_until INTEGER,
    checked_at INTEGER,
    supersedes_claim_id TEXT REFERENCES knowledge_claims(claim_id),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX idx_knowledge_claim_lookup
    ON knowledge_claims(subject_entity_id, predicate, status, checked_at);
CREATE TABLE knowledge_sources (
    source_id TEXT PRIMARY KEY,
    canonical_url TEXT NOT NULL UNIQUE,
    domain TEXT NOT NULL,
    publisher TEXT NOT NULL,
    source_class TEXT NOT NULL CHECK(source_class IN
      ('official','secondary','unofficial')),
    published_at INTEGER,
    fetched_at INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    evidence_excerpt TEXT NOT NULL
);
CREATE INDEX idx_knowledge_sources_domain
    ON knowledge_sources(domain, source_class, fetched_at);
CREATE TABLE knowledge_claim_evidence (
    evidence_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES knowledge_claims(claim_id),
    source_id TEXT REFERENCES knowledge_sources(source_id),
    observation_id TEXT REFERENCES knowledge_observations(observation_id),
    relation_kind TEXT NOT NULL CHECK(relation_kind IN
      ('supports','refutes','context')),
    created_at INTEGER NOT NULL,
    CHECK(source_id IS NOT NULL OR observation_id IS NOT NULL)
);
CREATE INDEX idx_knowledge_claim_evidence_claim
    ON knowledge_claim_evidence(claim_id, relation_kind);
CREATE TABLE group_conventions (
    convention_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    normalized_expression TEXT NOT NULL,
    resolved_entity_id TEXT REFERENCES knowledge_entities(entity_id),
    meaning_summary TEXT NOT NULL,
    evidence_observation_ids_json TEXT NOT NULL,
    distinct_actor_count INTEGER NOT NULL,
    distinct_scene_count INTEGER NOT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    status TEXT NOT NULL CHECK(status IN
      ('candidate','active','stale','rejected','disputed')),
    first_seen_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(group_id, normalized_expression, resolved_entity_id)
);
CREATE INDEX idx_group_convention_lookup
    ON group_conventions(group_id, normalized_expression, status);
CREATE TABLE group_topic_affinity (
    group_id TEXT NOT NULL,
    entity_id TEXT NOT NULL REFERENCES knowledge_entities(entity_id),
    qualified_mention_count INTEGER NOT NULL,
    distinct_actor_count INTEGER NOT NULL,
    distinct_scene_count INTEGER NOT NULL,
    salience REAL NOT NULL,
    first_seen_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(group_id, entity_id)
);
CREATE INDEX idx_group_topic_affinity_salience
    ON group_topic_affinity(group_id, salience DESC, last_seen_at DESC);
CREATE TABLE group_topic_mentions (
    group_id TEXT NOT NULL,
    entity_id TEXT NOT NULL REFERENCES knowledge_entities(entity_id),
    observation_id TEXT NOT NULL REFERENCES knowledge_observations(observation_id),
    source_event_id TEXT NOT NULL,
    author_ref TEXT NOT NULL,
    scene_ref TEXT NOT NULL,
    occurred_at INTEGER NOT NULL,
    PRIMARY KEY(group_id, entity_id, source_event_id)
);
CREATE INDEX idx_group_topic_mentions_projection
    ON group_topic_mentions(group_id, entity_id, occurred_at);
CREATE TABLE game_release_states (
    version_slot_id TEXT PRIMARY KEY,
    game_entity_id TEXT NOT NULL REFERENCES knowledge_entities(entity_id),
    official_label TEXT,
    region TEXT NOT NULL,
    platform TEXT NOT NULL,
    release_state TEXT NOT NULL CHECK(release_state IN
      ('future','current','past')),
    official_state TEXT NOT NULL CHECK(official_state IN
      ('none','teaser','preview','notice','released')),
    rumor_state TEXT NOT NULL CHECK(rumor_state IN
      ('none_observed','weak','corroborated','conflicted','stale')),
    announced_at INTEGER,
    release_at INTEGER,
    effective_until INTEGER,
    release_checked_at INTEGER,
    official_checked_at INTEGER,
    rumor_checked_at INTEGER,
    fresh_until INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','superseded','disputed')),
    revision INTEGER NOT NULL CHECK(revision > 0),
    UNIQUE(game_entity_id, region, platform, version_slot_id)
);
CREATE INDEX idx_game_release_lookup
    ON game_release_states(game_entity_id, region, platform, status, release_state);
CREATE TABLE negative_search_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    game_entity_id TEXT NOT NULL REFERENCES knowledge_entities(entity_id),
    query_intent TEXT NOT NULL,
    region TEXT,
    platform TEXT,
    covered_source_ids_json TEXT NOT NULL,
    checked_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    version_state_revision INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','invalidated','expired')),
    diagnostic_code TEXT
);
CREATE INDEX idx_negative_search_lookup
    ON negative_search_snapshots(
      game_entity_id, query_intent, region, platform, status, expires_at
    );
CREATE TABLE knowledge_jobs (
    job_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    job_kind TEXT NOT NULL CHECK(job_kind IN
      ('seed_import','official_daily_probe','instant_enrichment',
       'unknown_entity_learning','group_topic_warmup',
       'time_boundary_revalidation','correction_rebuild',
       'long_tail_official_refresh')),
    group_id TEXT,
    entity_id TEXT,
    request_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN
      ('pending','running','retry','completed','discarded')),
    attempt INTEGER NOT NULL DEFAULT 0,
    next_attempt_at INTEGER NOT NULL DEFAULT 0,
    diagnostic_code TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX idx_knowledge_jobs_due
    ON knowledge_jobs(status, next_attempt_at, created_at);
CREATE TABLE knowledge_usage (
    usage_id TEXT PRIMARY KEY,
    group_id TEXT,
    source_event_id TEXT,
    knowledge_ids_json TEXT NOT NULL,
    source_domains_json TEXT NOT NULL,
    query_intent_hash TEXT,
    latency_ms INTEGER NOT NULL,
    cache_hit INTEGER NOT NULL CHECK(cache_hit IN (0,1)),
    result_kind TEXT NOT NULL,
    diagnostic_code TEXT,
    recorded_at INTEGER NOT NULL
);
CREATE INDEX idx_knowledge_usage_time
    ON knowledge_usage(recorded_at, result_kind);
"""
