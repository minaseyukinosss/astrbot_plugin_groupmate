from __future__ import annotations

import pytest

from groupmate.social_runtime.persistence.event_store import (
    EventClaimError,
    SQLiteSocialEventStore,
)
from tests.factories import social_event_values
from groupmate.social_runtime.contracts import SocialEventEnvelope


def _event(**overrides):
    return SocialEventEnvelope.create(**social_event_values(**overrides))


def test_duplicate_event_returns_the_original_sequence(tmp_path):
    store = SQLiteSocialEventStore(tmp_path / "groupmate-social-runtime-v2.db")
    event = _event(event_id="evt-duplicate")

    first = store.append(event)
    second = store.append(event)

    assert first.inserted is True
    assert second.inserted is False
    assert first.sequence == second.sequence


def test_commit_records_effect_and_advances_cursor_once(tmp_path):
    store = SQLiteSocialEventStore(tmp_path / "groupmate-social-runtime-v2.db")
    store.append(_event())
    claimed = store.claim("persona:aemeath", after_sequence=0, limit=1)[0]

    first = store.commit(
        "persona:aemeath",
        claimed,
        effects=({"effect_id": "fx-1", "kind": "persona.created", "version": 1},),
    )
    second = store.commit(
        "persona:aemeath",
        claimed,
        effects=({"effect_id": "fx-1", "kind": "persona.created", "version": 1},),
    )

    effects = store.journal("corr-1")
    assert first == second
    assert first.last_sequence == claimed.sequence
    assert first.version == 1
    assert [(item.effect_id, item.effect_type) for item in effects] == [
        ("fx-1", "persona.created")
    ]


def test_failed_event_can_be_reclaimed_and_records_attempt(tmp_path):
    store = SQLiteSocialEventStore(tmp_path / "groupmate-social-runtime-v2.db")
    store.append(_event(event_id="evt-fail"))
    claimed = store.claim("group:aemeath:g1", 0, 1)[0]
    store.fail("group:aemeath:g1", claimed.sequence, "worker_timeout")

    reclaimed = store.claim("group:aemeath:g1", 0, 1)[0]

    assert reclaimed.event.event_id == "evt-fail"
    assert reclaimed.attempt == 2


def test_snapshot_loads_latest_version_without_affecting_cursor(tmp_path):
    store = SQLiteSocialEventStore(tmp_path / "groupmate-social-runtime-v2.db")
    store.save_snapshot("group:aemeath:g1", 2, {"scene_version": 2})
    store.save_snapshot("group:aemeath:g1", 4, {"scene_version": 4})

    snapshot = store.load_snapshot("group:aemeath:g1")

    assert snapshot.version == 4
    assert snapshot.payload == {"scene_version": 4}
    assert store.cursor("group:aemeath:g1").last_sequence == 0


def test_actor_cannot_commit_another_actors_claim(tmp_path):
    store = SQLiteSocialEventStore(tmp_path / "groupmate-social-runtime-v2.db")
    store.append(_event(event_id="evt-owned"))
    claimed = store.claim("group:aemeath:g1", 0, 1)[0]

    with pytest.raises(EventClaimError, match="owned by another actor"):
        store.commit(
            "group:aemeath:g2",
            claimed,
            ({"effect_id": "fx-wrong", "kind": "world.changed"},),
        )

    assert store.journal("corr-1") == ()
