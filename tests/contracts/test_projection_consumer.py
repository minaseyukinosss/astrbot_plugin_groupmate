from __future__ import annotations

import json

from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.control.projections import ProjectionConsumer
from groupmate.social_runtime.control.queries import ProjectionQueries
from groupmate.social_runtime.persistence.event_store import SQLiteSocialEventStore
from groupmate.social_runtime.persistence.schema import connect_database


def _commit_effect(
    store: SQLiteSocialEventStore,
    index: int,
    *,
    kind: str,
    payload: dict[str, object],
    event_type: str = "message.group",
    event_payload: dict[str, object] | None = None,
) -> None:
    event = SocialEventEnvelope.create(
        event_id=f"event:{index}",
        event_type=event_type,
        occurred_at=index,
        received_at=index,
        persona_id="aemeath",
        group_id="group-1",
        actor_id="member-1",
        source_message_id=f"message:{index}",
        correlation_id=f"corr:{index}",
        causation_id=None,
        payload=(
            {"text": f"raw message {index}"}
            if event_payload is None
            else event_payload
        ),
    )
    store.append(event)
    claimed = store.claim(
        "group:aemeath:group-1",
        index - 1,
        1,
        persona_id="aemeath",
        group_id="group-1",
    )[0]
    store.commit(
        "group:aemeath:group-1",
        claimed,
        effects=(
            {
                "effect_id": f"effect:{index}",
                "kind": kind,
                **payload,
            },
        ),
    )


def test_projection_consumers_have_independent_cursors_and_idempotent_effects(
    tmp_path,
):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    store = SQLiteSocialEventStore(path)
    _commit_effect(
        store,
        1,
        kind="group_world.projected",
        payload={"scene_version": 1},
    )
    _commit_effect(
        store,
        2,
        kind="memory.fact_recorded",
        payload={
            "subject_id": "member-1",
            "admin_visible": True,
            "fact_summary": "喜欢猫",
            "evidence_event_ids": ["event:2"],
        },
    )

    activity = ProjectionConsumer(path, "activity")
    scenes = ProjectionConsumer(path, "scenes")

    activity_first = activity.consume(1)
    scenes_first = scenes.consume(10)

    assert (activity_first.cursor, activity_first.applied) == (1, 1)
    assert (scenes_first.cursor, scenes_first.applied) == (2, 1)

    activity_second = activity.consume(10)
    activity_idle = activity.consume(10)
    assert (activity_second.cursor, activity_second.applied) == (2, 1)
    assert (activity_idle.cursor, activity_idle.applied) == (2, 0)

    with connect_database(path) as db:
        before = db.execute(
            "SELECT COUNT(*), MAX(projection_version) "
            "FROM control_projection_items WHERE projection_name='activity'"
        ).fetchone()
        db.execute(
            "UPDATE projection_cursors SET last_journal_rowid=0 "
            "WHERE projection_name='activity'"
        )

    replay = activity.consume(10)
    with connect_database(path) as db:
        after = db.execute(
            "SELECT COUNT(*), MAX(projection_version) "
            "FROM control_projection_items WHERE projection_name='activity'"
        ).fetchone()

    assert replay.applied == 0
    assert tuple(after) == tuple(before) == (2, 2)


def test_projection_queries_return_freshness_and_only_admin_safe_fact_summary(
    tmp_path,
):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    store = SQLiteSocialEventStore(path)
    _commit_effect(
        store,
        1,
        kind="memory.fact_recorded",
        payload={
            "subject_id": "qq:123456",
            "admin_visible": True,
            "fact_summary": "喜欢猫",
            "evidence_event_ids": ["qq:message:7788"],
            "prompt": "private system prompt",
            "chain_of_thought": "hidden reasoning",
            "secret": "top-secret-token",
            "raw_payload": {"text": "sensitive original message"},
            "internal_id": "database-row-9",
        },
    )

    ProjectionConsumer(path, "people").consume(10)
    queries = ProjectionQueries(path)
    result = queries.people(persona_id="aemeath", group_id="group-1")
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)

    assert result["projection"] == "people"
    assert result["as_of"] is not None
    assert result["cursor"] == 1
    assert result["projection_version"] == 1
    assert result["stale"] is False
    assert len(result["items"]) == 1
    assert result["items"][0]["summary"]["fact_summary"] == "喜欢猫"
    assert result["items"][0]["evidence_refs"]
    assert "qq:123456" not in encoded
    assert "qq:message:7788" not in encoded
    assert "private system prompt" not in encoded
    assert "hidden reasoning" not in encoded
    assert "top-secret-token" not in encoded
    assert "sensitive original message" not in encoded
    assert "database-row-9" not in encoded


def test_all_query_surfaces_are_projection_scoped_and_report_staleness(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    store = SQLiteSocialEventStore(path)
    _commit_effect(
        store,
        1,
        kind="group_world.projected",
        payload={"scene_version": 1},
    )
    for name in ProjectionConsumer.PROJECTION_NAMES:
        ProjectionConsumer(path, name).consume(10)

    queries = ProjectionQueries(path)
    scoped = {"persona_id": "aemeath", "group_id": "group-1"}
    surfaces = (
        queries.runtime(**scoped),
        queries.activity(**scoped),
        queries.scenes(**scoped),
        queries.people(**scoped),
        queries.culture(**scoped),
        queries.tasks(**scoped),
        queries.persona(**scoped),
        queries.governance(**scoped),
        queries.evaluation(**scoped),
        queries.health(**scoped),
    )
    bootstrap = queries.bootstrap(**scoped)

    assert {surface["projection"] for surface in surfaces} == set(
        ProjectionConsumer.PROJECTION_NAMES
    )
    assert all(surface["cursor"] == 1 for surface in surfaces)
    assert all(surface["stale"] is False for surface in surfaces)
    assert bootstrap["projection"] == "bootstrap"
    assert len(bootstrap["items"]) == len(ProjectionConsumer.PROJECTION_NAMES)

    _commit_effect(
        store,
        2,
        kind="group_world.projected",
        payload={"scene_version": 2},
    )

    assert queries.runtime(**scoped)["stale"] is True
    assert queries.bootstrap(**scoped)["stale"] is True


def test_task_projection_uses_only_committed_structured_source_event(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    store = SQLiteSocialEventStore(path)
    _commit_effect(
        store,
        1,
        kind="group_world.projected",
        payload={"scene_version": 1},
        event_type="capability.result",
        event_payload={
            "task_id": "task:private:42",
            "task_status": "succeeded",
            "delivery_relevant": True,
            "result": {"secret": "raw provider result"},
        },
    )

    ProjectionConsumer(path, "tasks").consume(10)
    result = ProjectionQueries(path).tasks(
        persona_id="aemeath", group_id="group-1"
    )
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)

    assert len(result["items"]) == 1
    assert result["items"][0]["kind"] == "capability.result"
    assert result["items"][0]["summary"] == {
        "delivery_relevant": True,
        "kind": "capability.result",
        "scene_version": 1,
        "task_status": "succeeded",
    }
    assert "task:private:42" not in encoded
    assert "raw provider result" not in encoded


def test_control_projection_carries_only_admin_command_correlation(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    store = SQLiteSocialEventStore(path)
    _commit_effect(
        store,
        1,
        kind="group_world.projected",
        payload={"scene_version": 1},
        event_type="control.runtime_paused",
        event_payload={"paused": True},
    )

    ProjectionConsumer(path, "governance").consume(10)
    result = ProjectionQueries(path).governance(
        persona_id="aemeath", group_id="group-1"
    )

    assert result["items"][0]["summary"]["command_id"] == "corr:1"
    assert result["items"][0]["summary"]["paused"] is True
