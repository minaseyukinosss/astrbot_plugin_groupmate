from __future__ import annotations

import asyncio

import pytest

from groupmate.social_runtime.contracts import PersonaSnapshot, SocialEventEnvelope
from groupmate.social_runtime.governor import GovernorResult
from groupmate.social_runtime.persistence.event_store import (
    JournalEffectIdentityConflict,
    SQLiteSocialEventStore,
)
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
                config_version=request.persona_snapshot.config_version,
                persona_state_version=request.persona_snapshot.state_version,
                frame_id="stale:frame",
                governor_result=GovernorResult(
                    "SILENCE", (), (), ("no_action",), None, ("hard_gate_v1",)
                ),
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


def test_pending_request_tracking_stays_bounded_without_worker_results(tmp_path):
    async def scenario():
        store = SQLiteSocialEventStore(tmp_path / "groupmate-social-runtime-v2.db")
        actor = GroupSceneActor(
            "aemeath", "885617919", store, _persona_snapshot
        )
        await actor.start()
        for index in range(30):
            store.append(_event(f"m{index}"))
        await actor.drain()
        pending_count = actor.pending_request_count
        await actor.close()
        return pending_count

    assert asyncio.run(scenario()) == 1


def test_scene_work_result_cannot_omit_governor_and_frozen_versions():
    with pytest.raises(TypeError):
        SceneWorkResult(
            request_id="scene:1",
            group_id="885617919",
            scene_version=1,
        )


def test_identical_governor_result_retry_returns_original_acceptance(tmp_path):
    async def scenario():
        actor = GroupSceneActor(
            "aemeath",
            "885617919",
            SQLiteSocialEventStore(tmp_path / "groupmate-social-runtime-v2.db"),
            _persona_snapshot,
        )
        await actor.start()
        direct = SocialEventEnvelope.create(
            **social_event_values(
                event_id="qq:retry",
                source_message_id="retry",
                correlation_id="corr:retry",
                payload={"text": "在吗", "direct_address": True},
            )
        )
        request = await actor.submit(direct)
        frame = request.attention_frames[0]
        result = SceneWorkResult(
            request_id=request.request_id,
            group_id=request.group_id,
            scene_version=request.scene_version,
            config_version=frame.config_version,
            persona_state_version=frame.persona_state_version,
            frame_id=frame.frame_id,
            governor_result=GovernorResult(
                "SILENCE", (), (), ("no_action",), None, ("hard_gate_v1",)
            ),
        )
        first = await actor.accept_result(result)
        retried = await actor.accept_result(result)
        await actor.close()
        return first, retried

    first, retried = asyncio.run(scenario())

    assert first is True
    assert retried is True


def test_changed_governor_result_retry_is_identity_conflict(tmp_path):
    async def scenario():
        actor = GroupSceneActor(
            "aemeath",
            "885617919",
            SQLiteSocialEventStore(tmp_path / "groupmate-social-runtime-v2.db"),
            _persona_snapshot,
        )
        await actor.start()
        direct = SocialEventEnvelope.create(
            **social_event_values(
                event_id="qq:conflict",
                source_message_id="conflict",
                correlation_id="corr:conflict",
                payload={"text": "在吗", "direct_address": True},
            )
        )
        request = await actor.submit(direct)
        frame = request.attention_frames[0]

        def result(reason):
            return SceneWorkResult(
                request_id=request.request_id,
                group_id=request.group_id,
                scene_version=request.scene_version,
                config_version=frame.config_version,
                persona_state_version=frame.persona_state_version,
                frame_id=frame.frame_id,
                governor_result=GovernorResult(
                    "SILENCE", (), (), (reason,), None, ("hard_gate_v1",)
                ),
            )

        assert await actor.accept_result(result("first")) is True
        with pytest.raises(JournalEffectIdentityConflict):
            await actor.accept_result(result("changed"))
        await actor.close()

    asyncio.run(scenario())


def test_explicit_discard_persists_auditable_reason(tmp_path):
    async def scenario():
        store = SQLiteSocialEventStore(
            tmp_path / "groupmate-social-runtime-v2.db"
        )
        actor = GroupSceneActor(
            "aemeath",
            "885617919",
            store,
            _persona_snapshot,
        )
        await actor.start()
        request = await actor.submit(_event("discard"))
        discarded = await actor.discard_work(
            request.request_id,
            "operator_cleanup",
        )
        stored = store.scene_work_request(actor.actor_key, request.request_id)
        await actor.close()
        return discarded, stored

    discarded, stored = asyncio.run(scenario())

    assert discarded is True
    assert stored.status == "stale"
    assert stored.resolution == {
        "kind": "explicit_discard",
        "reason_code": "operator_cleanup",
    }


def test_new_scene_supersede_persists_auditable_reason(tmp_path):
    async def scenario():
        store = SQLiteSocialEventStore(
            tmp_path / "groupmate-social-runtime-v2.db"
        )
        actor = GroupSceneActor(
            "aemeath",
            "885617919",
            store,
            _persona_snapshot,
        )
        await actor.start()
        old_request = await actor.submit(_event("old"))
        new_request = await actor.submit(_event("new"))
        stored = store.scene_work_request(actor.actor_key, old_request.request_id)
        await actor.close()
        return new_request, stored

    new_request, stored = asyncio.run(scenario())

    assert stored.status == "stale"
    assert stored.resolution == {
        "kind": "scene_superseded",
        "reason_code": "newer_scene_committed",
        "superseding_request_id": new_request.request_id,
    }
