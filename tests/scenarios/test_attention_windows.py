from __future__ import annotations

from dataclasses import replace

from groupmate.social_runtime.attention import (
    AttentionScheduler,
    PendingAttentionWindow,
)
from groupmate.social_runtime.contracts import PersonaSnapshot, SocialEventEnvelope
from groupmate.social_runtime.world import ConversationLease, GroupWorldProjector
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


def test_continuous_messages_flush_ambient_window_by_absolute_deadline():
    scheduler = AttentionScheduler()
    projector = GroupWorldProjector()
    world = projector.empty("885617919")

    for index, now in enumerate(range(100, 109), start=1):
        event = _message(index, now, f"u{index}")
        world = projector.apply(world, event)
        assert scheduler.on_event(event, world, _persona(), now=now) == ()
        if now < 108:
            assert scheduler.flush_due(now=now) == ()

    frames = scheduler.flush_due(now=108)

    assert len(frames) == 1
    assert frames[0].deadline == 108
    assert frames[0].focus_event_ids[-1] == "qq:m9"


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


def test_confirmed_alias_is_fast_without_ambient_worker():
    event = _message(1, 100, "u1")
    event = SocialEventEnvelope.create(
        **{
            **event.to_dict(),
            "payload": {
                **dict(event.payload),
                "direct_address": True,
                "address_kind": "ALIAS_PREFIX",
                "matched_alias": "小爱",
                "address_remainder": "说话",
            },
        }
    )
    projector = GroupWorldProjector()
    world = projector.apply(projector.empty(event.group_id), event)

    frame = AttentionScheduler().on_event(event, world, _persona(), now=100)[0]

    assert frame.trigger_kind == "FAST"
    assert frame.requested_workers == ()


def test_busy_ambient_window_keeps_only_recent_bounded_context():
    scheduler = AttentionScheduler()
    projector = GroupWorldProjector()
    world = projector.empty("885617919")

    for index in range(1, 21):
        event = _message(index, 100 + index, f"u{index}")
        world = projector.apply(world, event)
        assert scheduler.on_event(
            event, world, _persona(), now=event.occurred_at
        ) == ()

    window = scheduler.pending_window("885617919")
    assert window is not None
    assert window.focus_event_ids == tuple(
        f"qq:m{index}" for index in range(9, 21)
    )
    assert window.focus_topic_ids == tuple(
        f"m{index}" for index in range(17, 21)
    )
    assert window.candidate_audiences == tuple(
        f"u{index}" for index in range(13, 21)
    )

    frame = scheduler.flush_due(now=126)[0]
    assert frame.requested_workers == ("ambient_social_assessor",)


def test_restored_ambient_window_is_rebounded_before_dispatch():
    scheduler = AttentionScheduler()
    scheduler.restore_window(
        PendingAttentionWindow(
            group_id="885617919",
            scene_version=20,
            focus_topic_ids=tuple(f"t{index}" for index in range(1, 10)),
            focus_event_ids=tuple(f"e{index}" for index in range(1, 21)),
            candidate_audiences=tuple(f"u{index}" for index in range(1, 15)),
            window_started_at=92,
            deadline=100,
            persona_state_version=1,
            config_version=2,
        )
    )

    frame = scheduler.flush_due(now=100)[0]

    assert frame.focus_event_ids == tuple(f"e{index}" for index in range(9, 21))
    assert frame.focus_topic_ids == tuple(f"t{index}" for index in range(6, 10))
    assert frame.candidate_audiences == tuple(
        f"u{index}" for index in range(7, 15)
    )


def test_short_followup_keeps_dialogue_after_topic_projection_changes():
    projector = GroupWorldProjector()
    scheduler = AttentionScheduler()
    first = _message(1, 100, "u1")
    world = projector.apply(projector.empty("885617919"), first)
    lease = ConversationLease("u1", "m1", "reply:1", 100, 300, 5)
    world = replace(world, conversation_lease=lease)
    followup = SocialEventEnvelope.create(
        **social_event_values(
            event_id="qq:m2",
            source_message_id="m2",
            actor_id="u1",
            occurred_at=150,
            received_at=150,
            correlation_id="corr:m2",
            payload={"text": "然后呢"},
        )
    )
    world = projector.apply(world, followup)

    assert world.topic_for_message("m2").topic_id != lease.topic_id
    frame = scheduler.on_event(followup, world, _persona(), now=150)[0]
    assert frame.trigger_kind == "CONTINUATION"
    assert frame.requested_workers == ()


def test_new_direct_call_preempts_existing_dialogue_lease():
    projector = GroupWorldProjector()
    scheduler = AttentionScheduler()
    base = projector.apply(projector.empty("885617919"), _message(1, 100, "u1"))
    world = replace(
        base,
        conversation_lease=ConversationLease(
            "u1", "m1", "reply:1", 100, 300, 5
        ),
    )
    direct = SocialEventEnvelope.create(
        **social_event_values(
            event_id="qq:m2",
            source_message_id="m2",
            actor_id="u2",
            occurred_at=101,
            received_at=101,
            correlation_id="corr:m2",
            payload={"text": "小爱说话", "direct_address": True},
        )
    )
    world = projector.apply(world, direct)

    frame = scheduler.on_event(direct, world, _persona(), now=101)[0]
    assert frame.trigger_kind == "FAST"
    assert frame.candidate_audiences == ("u2",)


def test_external_capability_never_advances_social_lease():
    projector = GroupWorldProjector()
    scheduler = AttentionScheduler()
    base = projector.apply(projector.empty("885617919"), _message(1, 100, "u1"))
    lease = ConversationLease("u1", "m1", "reply:1", 100, 300, 5)
    world = replace(base, conversation_lease=lease)
    external = SocialEventEnvelope.create(
        **social_event_values(
            event_id="qq:m2",
            source_message_id="m2",
            actor_id="u1",
            occurred_at=101,
            received_at=101,
            correlation_id="corr:m2",
            payload={"text": "bq 开心", "social_eligible": False},
        )
    )
    world = projector.apply(world, external)

    assert scheduler.on_event(external, world, _persona(), now=101) == ()
    assert world.conversation_lease == lease
