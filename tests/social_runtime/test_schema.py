from __future__ import annotations

import sqlite3

import pytest

from groupmate.social_runtime.persistence.schema import (
    SCHEMA_VERSION,
    ForeignDatabaseError,
    connect_database,
    initialize_database,
    verify_schema,
)


REQUIRED_TABLES = {
    "social_runtime_schema",
    "inbox",
    "journal",
    "actor_cursors",
    "snapshots",
    "persona_state",
    "persona_effects",
    "group_world",
    "scene_work_requests",
    "attention_frames",
    "cognitive_observations",
    "candidate_intentions",
    "governor_results",
    "action_plans",
    "tasks",
    "task_events",
    "delivery_bundles",
    "outbox",
    "relationship_events",
    "relationship_projection",
    "impressions",
    "culture",
    "memories",
    "memory_tombstones",
    "config_versions",
    "governance_actions",
    "projection_cursors",
    "evaluation_labels",
    "member_identities",
    "member_aliases",
    "profile_observations",
    "profile_facts",
    "profile_episodes",
    "social_edges",
    "profile_snapshots",
    "group_portraits",
    "profile_preferences",
    "profile_audit",
    "member_style_settings",
    "member_speech_style_versions",
    "imitation_sessions",
    "knowledge_seeds",
    "knowledge_observations",
    "knowledge_entities",
    "knowledge_aliases",
    "group_knowledge_aliases",
    "knowledge_claims",
    "knowledge_sources",
    "knowledge_claim_evidence",
    "group_conventions",
    "group_topic_affinity",
    "group_topic_mentions",
    "game_release_states",
    "negative_search_snapshots",
    "knowledge_jobs",
    "knowledge_usage",
}

KNOWLEDGE_TABLES = {
    "knowledge_seeds",
    "knowledge_observations",
    "knowledge_entities",
    "knowledge_aliases",
    "group_knowledge_aliases",
    "knowledge_claims",
    "knowledge_sources",
    "knowledge_claim_evidence",
    "group_conventions",
    "group_topic_affinity",
    "group_topic_mentions",
    "game_release_states",
    "negative_search_snapshots",
    "knowledge_jobs",
    "knowledge_usage",
}


def test_new_database_bootstraps_complete_v4_schema(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"

    initialize_database(path)

    with connect_database(path) as db:
        names = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        version = db.execute(
            "SELECT version FROM social_runtime_schema WHERE singleton=1"
        ).fetchone()[0]
        assert REQUIRED_TABLES <= names
        assert SCHEMA_VERSION == version == 4
        assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert verify_schema(db) is None


def test_owned_v3_database_migrates_without_losing_runtime_profile_or_style_rows(
    tmp_path,
):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    initialize_database(path)
    with sqlite3.connect(str(path)) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        for table in KNOWLEDGE_TABLES:
            db.execute(f"DROP TABLE IF EXISTS {table}")
        db.execute(
            "UPDATE social_runtime_schema SET version=3 WHERE singleton=1"
        )
        db.execute(
            "INSERT INTO inbox(event_id,persona_id,envelope_json,received_at,status) "
            "VALUES('evt-v3','aemeath','{}',100,'pending')"
        )
        db.execute(
            "INSERT INTO member_style_settings("
            "group_id,member_id,enabled,enabled_at,updated_by,updated_at,version,"
            "collection_windows_json) VALUES('g1','u1',1,100,'admin',100,1,'[]')"
        )

    initialize_database(path)

    with connect_database(path) as db:
        assert db.execute(
            "SELECT version FROM social_runtime_schema WHERE singleton=1"
        ).fetchone()[0] == 4
        assert db.execute(
            "SELECT event_id FROM inbox WHERE event_id='evt-v3'"
        ).fetchone()[0] == "evt-v3"
        assert db.execute(
            "SELECT enabled FROM member_style_settings "
            "WHERE group_id='g1' AND member_id='u1'"
        ).fetchone()[0] == 1
        names = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert KNOWLEDGE_TABLES <= names


def test_knowledge_schema_rejects_invalid_scope_and_truth_states(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    initialize_database(path)

    with connect_database(path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO knowledge_observations("
                "observation_id,origin_class,scope_kind,group_id,author_ref,"
                "source_event_id,source_id,entity_hint,safe_summary,content_hash,"
                "occurred_at,recorded_at,status) VALUES("
                "'o1','human_chat','global','g1','opaque','e1',NULL,'原神',"
                "'summary',?,100,101,'pending')",
                ("a" * 64,),
            )
        db.execute(
            "INSERT INTO knowledge_entities("
            "entity_id,entity_type,canonical_name,canonical_game_id,status,"
            "created_at,updated_at) VALUES("
            "'game:g','game','游戏G','game:g','active',100,100)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO knowledge_claims("
                "claim_id,subject_entity_id,predicate,safe_summary,claim_kind,"
                "evidence_level,status,created_at,updated_at) VALUES("
                "'claim:1','game:g','genre','类型','guess','official',"
                "'active',100,100)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO game_release_states("
                "version_slot_id,game_entity_id,region,platform,release_state,"
                "official_state,rumor_state,fresh_until,status,revision) VALUES("
                "'slot:1','game:g','cn','all','current','confirmed',"
                "'none_observed',200,'active',1)"
            )


def test_empty_sqlite_shell_is_bootstrapped_after_deleted_database_race(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"

    # A stale page query can reopen the path after the old database was
    # deleted but before the new plugin finishes starting.
    with connect_database(path):
        pass

    initialize_database(path)

    with connect_database(path) as db:
        version = db.execute(
            "SELECT version FROM social_runtime_schema WHERE singleton=1"
        ).fetchone()[0]
        assert version == SCHEMA_VERSION
        assert verify_schema(db) is None


def test_initialize_is_idempotent_and_preserves_existing_events(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    initialize_database(path)
    with connect_database(path) as db:
        db.execute(
            "INSERT INTO inbox(event_id, persona_id, envelope_json, received_at, status) "
            "VALUES('evt-1', 'aemeath', '{}', 100, 'pending')"
        )
        db.commit()

    initialize_database(path)

    with connect_database(path) as db:
        assert db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0] == 1


def test_legacy_database_name_is_rejected_without_creating_a_file(tmp_path):
    path = tmp_path / "groupmate.db"

    with pytest.raises(ForeignDatabaseError, match="legacy database path"):
        initialize_database(path)

    assert not path.exists()


def test_existing_foreign_database_is_rejected_without_modification(tmp_path):
    path = tmp_path / "unrelated.db"
    with sqlite3.connect(str(path)) as db:
        db.execute("CREATE TABLE owner_data(value TEXT NOT NULL)")
        db.execute("INSERT INTO owner_data(value) VALUES('keep-me')")

    with pytest.raises(ForeignDatabaseError, match="not a Social Runtime database"):
        initialize_database(path)

    with sqlite3.connect(str(path)) as db:
        assert db.execute("SELECT value FROM owner_data").fetchone()[0] == "keep-me"
        assert db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='social_runtime_schema'"
        ).fetchone()[0] == 0


def test_outbox_rejects_unknown_status(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    initialize_database(path)

    with connect_database(path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO outbox("
            "part_id, bundle_id, persona_id, group_id, idempotency_key, status, "
            "payload_json, expires_at"
            ") VALUES('p1', 'b1', 'aemeath', 'g1', 'key1', 'maybe', '{}', 100)"
        )
