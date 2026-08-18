from __future__ import annotations

from groupmate.social_runtime.attention import AttentionScheduler
from groupmate.social_runtime.contracts import PersonaSnapshot, SocialEventEnvelope
from groupmate.social_runtime.world import GroupWorldProjector
from tests.factories import social_event_values


def _persona():
    return PersonaSnapshot(
        persona_id="aemeath",
        state_version=1,
        config_version=2,
        presence="awake",
        energy=100,
        mode="social",
        modifiers=(),
    )


def _message(index, now, actor_id):
    return SocialEventEnvelope.create(
        **social_event_values(
            event_id=f"qq:m{index}",
            source_message_id=f"m{index}",
            actor_id=actor_id,
            occurred_at=now,
            received_at=now,
            correlation_id=f"corr:m{index}",
            payload={"text": f"第 {index} 句"},
        )
    )


def test_ambient_window_merges_continuous_messages_and_refreshes_scene_version():
    scheduler = AttentionScheduler()
    projector = GroupWorldProjector()
    world = projector.empty("885617919")

    first = _message(1, 100, "u1")
    world = projector.apply(world, first)
    assert scheduler.on_event(first, world, _persona(), now=100) == ()

    second = _message(2, 101, "u2")
    world = projector.apply(world, second)
    assert scheduler.on_event(second, world, _persona(), now=101) == ()

    assert scheduler.flush_due(now=102) == ()
    frames = scheduler.flush_due(now=103)

    assert len(frames) == 1
    frame = frames[0]
    assert frame.trigger_kind == "AMBIENT"
    assert frame.scene_version == world.scene_version == 2
    assert frame.focus_event_ids == ("qq:m1", "qq:m2")
    assert frame.candidate_audiences == ("u1", "u2")


def test_high_velocity_group_uses_longer_bounded_window():
    scheduler = AttentionScheduler()
    projector = GroupWorldProjector()
    world = projector.empty("885617919")

    for index in range(1, 14):
        event = _message(index, 100 + index // 4, f"u{index % 3}")
        world = projector.apply(world, event)
        assert scheduler.on_event(
            event, world, _persona(), now=event.occurred_at
        ) == ()

    last_at = event.occurred_at
    assert scheduler.flush_due(now=last_at + 4) == ()
    frames = scheduler.flush_due(now=last_at + 5)

    assert len(frames) == 1
    assert frames[0].scene_version == 13
    assert frames[0].deadline == last_at + 5


def test_undispatched_ambient_frame_refreshes_when_fast_event_advances_scene():
    scheduler = AttentionScheduler()
    projector = GroupWorldProjector()
    world = projector.empty("885617919")
    ambient = _message(1, 100, "u1")
    world = projector.apply(world, ambient)
    scheduler.on_event(ambient, world, _persona(), now=100)

    direct = SocialEventEnvelope.create(
        **social_event_values(
            event_id="qq:m2",
            source_message_id="m2",
            actor_id="u2",
            occurred_at=101,
            received_at=101,
            correlation_id="corr:m2",
            payload={"text": "@小爱", "mentions_bot": True},
        )
    )
    world = projector.apply(world, direct)
    fast = scheduler.on_event(direct, world, _persona(), now=101)[0]
    ambient_frame = scheduler.flush_due(now=102)[0]

    assert fast.scene_version == 2
    assert ambient_frame.scene_version == 2
    assert ambient_frame.focus_event_ids == ("qq:m1",)
