from __future__ import annotations

import asyncio

from groupmate.social_runtime.contracts import RuntimeMode, SocialEventEnvelope
from groupmate.social_runtime.manager import SocialRuntimeManager
from tests.factories import social_event_values


def _event(message_id="m1", group_id="885617919"):
    return SocialEventEnvelope.create(
        **social_event_values(
            event_id=f"qq:{message_id}",
            source_message_id=message_id,
            group_id=group_id,
            correlation_id=f"corr:{message_id}",
        )
    )


def test_manager_routes_new_events_once_and_drains_group_actor(tmp_path):
    async def scenario():
        manager = SocialRuntimeManager(
            database_path=tmp_path / "groupmate-social-runtime-v2.db",
            persona_id="aemeath",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("885617919",),
        )
        await manager.start()
        first = await manager.ingest(_event())
        duplicate = await manager.ingest(_event())
        await manager.drain()
        state = await manager.group_snapshot("885617919")
        await manager.close()
        return first, duplicate, state

    first, duplicate, state = asyncio.run(scenario())

    assert first.inserted is True
    assert duplicate.inserted is False
    assert state.scene_version == 1


def test_manager_ignores_groups_outside_explicit_allowlist(tmp_path):
    async def scenario():
        manager = SocialRuntimeManager(
            database_path=tmp_path / "groupmate-social-runtime-v2.db",
            persona_id="aemeath",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("885617919",),
        )
        await manager.start()
        result = await manager.ingest(_event(group_id="other"))
        await manager.close()
        return result, manager.event_store.event_ids()

    result, event_ids = asyncio.run(scenario())

    assert result is None
    assert event_ids == ()
