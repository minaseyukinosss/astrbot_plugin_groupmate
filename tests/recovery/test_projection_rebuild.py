from __future__ import annotations

import asyncio

import pytest

from groupmate.social_runtime.contracts import RuntimeMode, SocialEventEnvelope
from groupmate.social_runtime.control.projections import ProjectionConsumer
from groupmate.social_runtime.control.queries import ProjectionQueries
from groupmate.social_runtime.manager import SocialRuntimeManager
from groupmate.social_runtime.persistence.schema import connect_database
from tests.factories import social_event_values


def _event(index: int) -> SocialEventEnvelope:
    return SocialEventEnvelope.create(
        **social_event_values(
            event_id=f"qq:projection:{index}",
            source_message_id=f"projection:{index}",
            occurred_at=index,
            received_at=index,
            correlation_id=f"corr:projection:{index}",
            payload={"text": f"消息 {index}"},
        )
    )


def test_deleted_projection_can_be_fully_rebuilt_from_journal(tmp_path):
    async def scenario():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        manager = SocialRuntimeManager(
            database_path=path,
            persona_id="aemeath",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("885617919",),
        )
        await manager.start()
        for index in range(1, 4):
            await manager.ingest(_event(index))
        await manager.drain()

        consumer = ProjectionConsumer(path, "activity")
        consumer.consume(2)
        consumer.consume(2)
        queries = ProjectionQueries(path)
        before = queries.activity(
            persona_id="aemeath", group_id="885617919"
        )

        with connect_database(path) as db:
            db.execute(
                "DELETE FROM control_projection_items "
                "WHERE projection_name='activity'"
            )
            db.execute(
                "DELETE FROM control_projection_applied "
                "WHERE projection_name='activity'"
            )
            db.execute(
                "DELETE FROM projection_cursors "
                "WHERE projection_name='activity'"
            )

        rebuilt = consumer.rebuild("activity")
        after = queries.activity(
            persona_id="aemeath", group_id="885617919"
        )
        await manager.close()
        return before, rebuilt, after

    before, rebuilt, after = asyncio.run(scenario())

    assert rebuilt == 3
    assert after == before


def test_projection_failure_does_not_block_actor_or_advance_projection_cursor(
    tmp_path, monkeypatch
):
    async def scenario():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        manager = SocialRuntimeManager(
            database_path=path,
            persona_id="aemeath",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("885617919",),
        )
        await manager.start()
        await manager.ingest(_event(1))
        await manager.drain()

        consumer = ProjectionConsumer(path, "activity")

        def explode(*_args, **_kwargs):
            raise RuntimeError("projection consumer crashed")

        monkeypatch.setattr(consumer, "_project_effect", explode)
        with pytest.raises(RuntimeError, match="consumer crashed"):
            consumer.consume(10)

        with connect_database(path) as db:
            cursor = db.execute(
                "SELECT last_journal_rowid FROM projection_cursors "
                "WHERE projection_name='activity'"
            ).fetchone()

        await manager.ingest(_event(2))
        await manager.drain()
        snapshot = await manager.group_snapshot("885617919")
        await manager.close()
        return cursor, snapshot.scene_version

    cursor, scene_version = asyncio.run(scenario())

    assert cursor is None
    assert scene_version == 2
