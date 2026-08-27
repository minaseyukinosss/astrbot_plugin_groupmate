from __future__ import annotations

import sqlite3

from groupmate.social_runtime.persistence.schema import (
    SCHEMA_VERSION,
    connect_database,
    initialize_database,
)


_V1_TABLES = {
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
}

_PROFILE_TABLES = {
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
}

_MEMBER_STYLE_TABLES = {
    "member_style_settings",
    "member_speech_style_versions",
    "imitation_sessions",
}


def _create_v1_database(path) -> None:
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE social_runtime_schema ("
            "singleton INTEGER PRIMARY KEY, version INTEGER NOT NULL, "
            "created_at INTEGER NOT NULL)"
        )
        db.execute(
            "INSERT INTO social_runtime_schema VALUES(1, 1, 1)"
        )
        for name in sorted(_V1_TABLES):
            db.execute(f"CREATE TABLE {name} (marker TEXT)")
        db.execute("INSERT INTO culture(marker) VALUES('preserve-me')")


def test_v1_database_upgrades_in_place_without_losing_runtime_rows(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    _create_v1_database(path)

    initialize_database(path)

    with connect_database(path) as db:
        version = db.execute(
            "SELECT version FROM social_runtime_schema WHERE singleton=1"
        ).fetchone()[0]
        names = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        marker = db.execute("SELECT marker FROM culture").fetchone()[0]
    assert SCHEMA_VERSION == version == 3
    assert _PROFILE_TABLES <= names
    assert _MEMBER_STYLE_TABLES <= names
    assert marker == "preserve-me"


def test_fresh_database_contains_profile_schema(tmp_path):
    path = tmp_path / "fresh-social-runtime.db"

    initialize_database(path)

    with connect_database(path) as db:
        names = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert _PROFILE_TABLES | _MEMBER_STYLE_TABLES <= names
