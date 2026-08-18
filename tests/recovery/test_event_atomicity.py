from __future__ import annotations

import sqlite3

import pytest

from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.persistence.event_store import SQLiteSocialEventStore
from groupmate.social_runtime.persistence.schema import connect_database
from tests.factories import social_event_values


def test_cursor_failure_rolls_back_journal_and_event_commit(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    store = SQLiteSocialEventStore(path)
    event = SocialEventEnvelope.create(**social_event_values())
    store.append(event)
    claimed = store.claim("persona:aemeath", 0, 1)[0]
    with connect_database(path) as db:
        db.execute(
            "CREATE TRIGGER reject_cursor BEFORE INSERT ON actor_cursors "
            "BEGIN SELECT RAISE(ABORT, 'simulated cursor crash'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="simulated cursor crash"):
        store.commit(
            "persona:aemeath",
            claimed,
            ({"effect_id": "fx-crash", "kind": "persona.created"},),
        )

    assert store.journal("corr-1") == ()
    assert store.cursor("persona:aemeath").last_sequence == 0
    assert store.claim("persona:aemeath", 0, 1)[0].event.event_id == "evt-1"
