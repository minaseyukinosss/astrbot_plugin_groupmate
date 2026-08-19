from __future__ import annotations

import asyncio
import json

from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.control.projections import ProjectionConsumer
from groupmate.social_runtime.control.stream import ProjectionStream
from groupmate.social_runtime.persistence.event_store import SQLiteSocialEventStore


def _commit(store, index: int, *, kind: str, payload: dict[str, object]):
    event = SocialEventEnvelope.create(
        event_id=f"event:sse:{index}",
        event_type=kind,
        occurred_at=index,
        received_at=index,
        persona_id="aemeath",
        group_id="group-1",
        actor_id="member-1",
        source_message_id=f"message:{index}",
        correlation_id=f"corr:sse:{index}",
        causation_id=None,
        payload=payload,
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
                "effect_id": f"effect:sse:{index}",
                "kind": "group_world.projected",
                "scene_version": index,
            },
        ),
    )


def test_sse_resumes_after_last_event_id_with_fixed_privacy_trimmed_shape(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    store = SQLiteSocialEventStore(path)
    _commit(
        store,
        1,
        kind="capability.result",
        payload={
            "task_id": "task:private:1",
            "task_status": "succeeded",
            "result": {"secret": "provider raw result"},
            "prompt": "private prompt",
            "chain_of_thought": "private reasoning",
            "auth_code": "123456",
        },
    )
    _commit(
        store,
        2,
        kind="control.runtime_paused",
        payload={"paused": True, "secret": "must-not-stream"},
    )
    ProjectionConsumer(path, "tasks").consume(10)
    ProjectionConsumer(path, "governance").consume(10)
    stream = ProjectionStream(path, retention=20)

    first = stream.read(
        last_event_id=None,
        persona_id="aemeath",
        group_id="group-1",
        limit=1,
    )
    second = stream.read(
        last_event_id=str(first.events[-1]["cursor"]),
        persona_id="aemeath",
        group_id="group-1",
        limit=10,
    )

    assert first.snapshot_required is False
    assert second.snapshot_required is False
    assert len(first.events) == 1
    assert len(second.events) == 1
    assert set(first.events[0]) == {
        "cursor",
        "kind",
        "scope",
        "entity",
        "projection_version",
        "summary",
    }
    assert int(second.events[0]["cursor"]) > int(first.events[0]["cursor"])
    encoded = json.dumps(first.events + second.events, ensure_ascii=False)
    assert "provider raw result" not in encoded
    assert "private prompt" not in encoded
    assert "private reasoning" not in encoded
    assert "must-not-stream" not in encoded
    assert "123456" not in encoded

    wire = stream.encode(first)
    assert f"id: {first.events[0]['cursor']}\n" in wire
    assert "event: projection\n" in wire
    assert wire.endswith("\n\n")


def test_expired_sse_cursor_returns_snapshot_required(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    store = SQLiteSocialEventStore(path)
    for index in range(1, 5):
        _commit(
            store,
            index,
            kind="control.runtime_paused",
            payload={"paused": bool(index % 2)},
        )
    ProjectionConsumer(path, "governance").consume(10)
    stream = ProjectionStream(path, retention=2)

    batch = stream.read(
        last_event_id="0",
        persona_id="aemeath",
        group_id="group-1",
        limit=10,
    )

    assert batch.snapshot_required is True
    assert batch.events == (
        {
            "cursor": batch.latest_cursor,
            "kind": "snapshot_required",
            "scope": {"persona_id": "aemeath", "group_id": "group-1"},
            "entity": None,
            "projection_version": 0,
            "summary": {"reason": "cursor_expired"},
        },
    )


def test_subscription_stays_open_and_delivers_later_projection_batches(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    store = SQLiteSocialEventStore(path)
    _commit(
        store,
        1,
        kind="control.runtime_paused",
        payload={"paused": True},
    )
    projection = ProjectionConsumer(path, "governance")
    projection.consume(10)
    stream = ProjectionStream(path, retention=20)

    async def scenario():
        subscription = stream.subscribe(
            last_event_id=None,
            persona_id="aemeath",
            group_id="group-1",
            poll_seconds=0,
        )
        first = await anext(subscription)
        _commit(
            store,
            2,
            kind="control.runtime_resumed",
            payload={"paused": False},
        )
        projection.consume(10)
        second = await anext(subscription)
        await subscription.aclose()
        return first, second

    first, second = asyncio.run(scenario())

    assert "control.runtime_paused" in first
    assert "control.runtime_resumed" in second
    assert first != second
