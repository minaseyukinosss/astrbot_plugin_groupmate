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


def test_manager_resolves_reply_target_before_scene_projection(tmp_path):
    async def scenario():
        manager = SocialRuntimeManager(
            database_path=tmp_path / "groupmate-social-runtime-v2.db",
            persona_id="aemeath",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("885617919",),
        )
        await manager.start()
        original = SocialEventEnvelope.create(
            **social_event_values(
                event_id="qq:m1",
                source_message_id="m1",
                actor_id="bot-1",
                correlation_id="corr:m1",
                payload={"text": "上一句", "bot_id": "bot-1", "is_self": True},
            )
        )
        reply = SocialEventEnvelope.create(
            **social_event_values(
                event_id="qq:m2",
                source_message_id="m2",
                actor_id="u1",
                correlation_id="corr:m2",
                payload={
                    "text": "接着说",
                    "bot_id": "bot-1",
                    "reply_to": "m1",
                    "reply_to_actor_id": None,
                    "reply_to_bot": False,
                },
            )
        )
        await manager.ingest(original)
        await manager.ingest(reply)
        stored = manager.event_store.event_by_source_message(
            "aemeath", "885617919", "qq", "m2"
        )
        await manager.drain()
        state = await manager.group_snapshot("885617919")
        await manager.close()
        return stored, state

    stored, state = asyncio.run(scenario())

    assert stored is not None
    assert stored.payload["reply_to_actor_id"] == "bot-1"
    assert stored.payload["reply_to_bot"] is True
    assert state.interaction_edges[-1].target_actor_id == "bot-1"
    assert state.recent_presence.last_bot_event_at is not None


def test_manager_exposes_pending_ambient_deadline(tmp_path):
    async def scenario():
        manager = SocialRuntimeManager(
            database_path=tmp_path / "groupmate-social-runtime-v2.db",
            persona_id="aemeath",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("885617919",),
        )
        await manager.start()
        event = SocialEventEnvelope.create(
            **social_event_values(
                event_id="qq:ambient",
                source_message_id="ambient",
                occurred_at=100,
                received_at=100,
                correlation_id="corr:ambient",
                payload={"text": "路过说一句"},
            )
        )
        await manager.ingest(event)
        await manager.drain(now=100)
        deadline = await manager.next_attention_deadline()
        await manager.close()
        return deadline

    assert asyncio.run(scenario()) == 102
