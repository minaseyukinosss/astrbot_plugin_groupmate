from __future__ import annotations

import asyncio

from groupmate.social_runtime.contracts import PersonaSnapshot, SocialEventEnvelope
from groupmate.social_runtime.persistence.event_store import SQLiteSocialEventStore
from groupmate.social_runtime.persistence.schema import connect_database
from groupmate.social_runtime.scene_actor import GroupSceneActor, SceneWorkResult
from tests.factories import social_event_values


async def _persona_snapshot():
    return PersonaSnapshot(
        persona_id="aemeath",
        state_version=1,
        config_version=2,
        presence="awake",
        energy=95,
        mode="social",
        modifiers=(),
    )


def _event(index):
    return SocialEventEnvelope.create(
        **social_event_values(
            event_id=f"qq:m{index}",
            source_message_id=f"m{index}",
            occurred_at=index,
            received_at=index,
            correlation_id=f"corr:m{index}",
            payload={"text": f"消息 {index}"},
        )
    )


def test_scene_actor_recovers_identical_snapshot_and_cursor_after_restart(tmp_path):
    async def scenario():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        store = SQLiteSocialEventStore(path)
        first = GroupSceneActor(
            "aemeath", "885617919", store, _persona_snapshot
        )
        await first.start()
        await first.submit(_event(1))
        await first.submit(_event(2))
        before = await first.snapshot()
        cursor_before = store.cursor(first.actor_key)
        await first.close()

        recovered = GroupSceneActor(
            "aemeath",
            "885617919",
            SQLiteSocialEventStore(path),
            _persona_snapshot,
        )
        await recovered.start()
        after = await recovered.snapshot()
        cursor_after = store.cursor(recovered.actor_key)
        await recovered.close()
        return before, after, cursor_before, cursor_after

    before, after, cursor_before, cursor_after = asyncio.run(scenario())

    assert after == before
    assert cursor_after == cursor_before


def test_scene_actor_replays_committed_event_when_snapshot_lags_cursor(tmp_path):
    async def scenario():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        store = SQLiteSocialEventStore(path)
        actor_key = "group:aemeath:885617919"
        store.append(_event(1))
        claimed = store.claim(
            actor_key,
            0,
            1,
            persona_id="aemeath",
            group_id="885617919",
        )[0]
        store.commit(
            actor_key,
            claimed,
            ({"effect_id": "world:replay:m1", "kind": "group_world.projected"},),
        )
        assert store.load_snapshot(actor_key) is None

        recovered = GroupSceneActor(
            "aemeath", "885617919", store, _persona_snapshot
        )
        await recovered.start()
        state = await recovered.snapshot()
        await recovered.close()
        return state

    state = asyncio.run(scenario())

    assert state.scene_version == 1
    assert state.topic_for_message("m1").root_event_id == "m1"


def test_post_commit_snapshot_failure_never_marks_committed_event_failed(tmp_path):
    class FailFirstSnapshotStore(SQLiteSocialEventStore):
        def __init__(self, path):
            super().__init__(path)
            self.fail_next_snapshot = True

        def save_snapshot(self, actor_key, version, payload):
            if self.fail_next_snapshot:
                self.fail_next_snapshot = False
                raise OSError("injected snapshot write failure")
            return super().save_snapshot(actor_key, version, payload)

    async def scenario():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        store = FailFirstSnapshotStore(path)
        first = GroupSceneActor(
            "aemeath", "885617919", store, _persona_snapshot
        )
        first.SNAPSHOT_INTERVAL = 1
        await first.start()
        request = await first.submit(_event(1))
        with connect_database(path) as db:
            status = db.execute(
                "SELECT status FROM inbox WHERE event_id='qq:m1'"
            ).fetchone()[0]

        recovered = GroupSceneActor(
            "aemeath",
            "885617919",
            SQLiteSocialEventStore(path),
            _persona_snapshot,
        )
        await recovered.start()
        state = await recovered.snapshot()
        await recovered.close()
        await first.close()
        return request, status, state

    request, status, state = asyncio.run(scenario())

    assert request.scene_version == 1
    assert status == "committed"
    assert state.scene_version == 1


def test_pending_scene_work_is_reissued_after_restart_until_resolved(tmp_path):
    async def scenario():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        first = GroupSceneActor(
            "aemeath",
            "885617919",
            SQLiteSocialEventStore(path),
            _persona_snapshot,
        )
        await first.start()
        original = await first.submit(_event(1))
        await first.close()

        recovered = GroupSceneActor(
            "aemeath",
            "885617919",
            SQLiteSocialEventStore(path),
            _persona_snapshot,
        )
        await recovered.start()
        first_drain = await recovered.drain()
        second_drain = await recovered.drain()
        accepted = await recovered.accept_result(
            SceneWorkResult(
                request_id=original.request_id,
                group_id=original.group_id,
                scene_version=original.scene_version,
                observations=(),
            )
        )
        await recovered.close()

        final = GroupSceneActor(
            "aemeath",
            "885617919",
            SQLiteSocialEventStore(path),
            _persona_snapshot,
        )
        await final.start()
        after_resolution = await final.drain()
        await final.close()
        return original, first_drain, second_drain, accepted, after_resolution

    original, first_drain, second_drain, accepted, after_resolution = asyncio.run(
        scenario()
    )

    assert first_drain == (original,)
    assert second_drain == ()
    assert accepted is True
    assert after_resolution == ()
