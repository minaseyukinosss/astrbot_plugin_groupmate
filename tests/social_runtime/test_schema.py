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
    "group_world",
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
}


def test_new_database_bootstraps_complete_v1_schema(tmp_path):
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
        assert SCHEMA_VERSION == version == 1
        assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
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
