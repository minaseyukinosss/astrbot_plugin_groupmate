from __future__ import annotations

import asyncio

from groupmate.social_runtime.contracts import PersonaSnapshot, SocialEventEnvelope
from groupmate.social_runtime.persistence.event_store import SQLiteSocialEventStore
from groupmate.social_runtime.scene_actor import (
    GroupSceneActor,
    SceneWorkResult,
)
from tests.factories import social_event_values


def _event(message_id="m1", text="早"):
    return SocialEventEnvelope.create(
        **social_event_values(
            event_id=f"qq:{message_id}",
            source_message_id=message_id,
            correlation_id=f"corr:{message_id}",
            payload={"text": text},
        )
    )


async def _persona_snapshot():
    return PersonaSnapshot(
        persona_id="aemeath",
        state_version=3,
        config_version=7,
        presence="awake",
        energy=90,
        mode="social",
        modifiers=("warm",),
    )


def test_scene_actor_projects_event_and_freezes_work_request_context(tmp_path):
    async def scenario():
        actor = GroupSceneActor(
            "aemeath",
            "885617919",
            SQLiteSocialEventStore(tmp_path / "groupmate-social-runtime-v2.db"),
            _persona_snapshot,
        )
        await actor.start()
        request = await actor.submit(_event())
        state = await actor.snapshot()
        await actor.close()
        return request, state

    request, state = asyncio.run(scenario())

    assert request.trigger_event_id == "qq:m1"
    assert request.scene_version == state.scene_version == 1
    assert request.persona_snapshot.state_version == 3
    assert request.persona_snapshot.config_version == 7
    assert state.topic_for_message("m1").root_event_id == "m1"


def test_stale_external_result_is_rejected_without_changing_scene(tmp_path):
    async def scenario():
        actor = GroupSceneActor(
            "aemeath",
            "885617919",
            SQLiteSocialEventStore(tmp_path / "groupmate-social-runtime-v2.db"),
            _persona_snapshot,
        )
        await actor.start()
        request = await actor.submit(_event())
        before = await actor.snapshot()
        accepted = await actor.accept_result(
            SceneWorkResult(
                request_id=request.request_id,
                group_id=request.group_id,
                scene_version=request.scene_version - 1,
                observations=({"kind": "topic", "topic_id": "wrong"},),
            )
        )
        after = await actor.snapshot()
        await actor.close()
        return accepted, before, after

    accepted, before, after = asyncio.run(scenario())

    assert accepted is False
    assert after == before


def test_actor_drain_only_claims_its_own_group_events(tmp_path):
    async def scenario():
        store = SQLiteSocialEventStore(tmp_path / "groupmate-social-runtime-v2.db")
        other = SocialEventEnvelope.create(
            **social_event_values(
                event_id="qq:other",
                source_message_id="other",
                group_id="other-group",
                correlation_id="corr:other",
            )
        )
        store.append(other)
        store.append(_event("m1"))
        actor = GroupSceneActor(
            "aemeath", "885617919", store, _persona_snapshot
        )
        await actor.start()
        requests = await actor.drain()
        state = await actor.snapshot()
        await actor.close()
        return requests, state, store.cursor("group:aemeath:885617919")

    requests, state, cursor = asyncio.run(scenario())

    assert [request.trigger_event_id for request in requests] == ["qq:m1"]
    assert state.scene_version == 1
    assert cursor.last_sequence == 2
