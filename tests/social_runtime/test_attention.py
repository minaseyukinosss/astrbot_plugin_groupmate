from __future__ import annotations

from groupmate.social_runtime.attention import AttentionScheduler
from groupmate.social_runtime.contracts import PersonaSnapshot, SocialEventEnvelope
from groupmate.social_runtime.world import GroupWorldProjector
from tests.factories import social_event_values


def _persona():
    return PersonaSnapshot(
        persona_id="aemeath",
        state_version=3,
        config_version=7,
        presence="awake",
        energy=90,
        mode="social",
        modifiers=(),
    )


def _event(
    message_id="m1",
    *,
    actor_id="u1",
    occurred_at=100,
    event_type="platform.message",
    payload=None,
):
    return SocialEventEnvelope.create(
        **social_event_values(
            event_id=f"qq:{message_id}",
            event_type=event_type,
            source_message_id=message_id,
            actor_id=actor_id,
            occurred_at=occurred_at,
            received_at=occurred_at,
            correlation_id=f"corr:{message_id}",
            payload=payload or {"text": "早"},
        )
    )


def _world_with(event):
    projector = GroupWorldProjector()
    return projector.apply(projector.empty(event.group_id), event)


def _world_with_lease(*, target_id="u1", topic_id="m1", expires_at=280):
    projector = GroupWorldProjector()
    root = _event(topic_id, actor_id=target_id, occurred_at=90)
    state = projector.apply(projector.empty(root.group_id), root)
    lease = SocialEventEnvelope.create(
        **social_event_values(
            event_id="lease:open",
            event_type="conversation.lease_opened",
            source_message_id=None,
            actor_id=None,
            occurred_at=100,
            received_at=100,
            correlation_id="corr:lease:open",
            payload={
                "target_id": target_id,
                "topic_id": topic_id,
                "source_plan_id": "reply:open",
                "opened_at": 100,
                "expires_at": expires_at,
                "remaining_turns": 5,
            },
        )
    )
    return projector.apply(state, lease)


def test_direct_mention_immediately_creates_fast_frame():
    event = _event(
        payload={"text": "小爱在吗", "mentions": ["323537051"], "mentions_bot": True}
    )
    world = _world_with(event)

    frames = AttentionScheduler().on_event(event, world, _persona(), now=100)

    assert len(frames) == 1
    frame = frames[0]
    assert frame.trigger_kind == "FAST"
    assert frame.scene_version == world.scene_version
    assert frame.candidate_audiences == ("u1",)
    assert frame.focus_event_ids == ("qq:m1",)
    assert frame.focus_topic_ids == ("m1",)
    assert frame.deadline == 100
    assert frame.requested_workers == ()


def test_matching_live_lease_creates_continuation_frame_without_waiting():
    projector = GroupWorldProjector()
    world = _world_with_lease()
    event = _event(
        "m2",
        actor_id="u1",
        occurred_at=120,
        payload={"text": "然后呢", "reply_to": "m1"},
    )
    world = projector.apply(world, event)

    frame = AttentionScheduler().on_event(event, world, _persona(), now=120)[0]

    assert frame.trigger_kind == "CONTINUATION"
    assert frame.candidate_audiences == ("u1",)
    assert frame.focus_topic_ids == ("m1",)
    assert frame.requested_workers == ()


def test_mismatched_or_expired_lease_falls_back_to_ambient_window():
    projector = GroupWorldProjector()
    scheduler = AttentionScheduler()
    member_event = _event(
        "m2",
        actor_id="u2",
        occurred_at=120,
        payload={"text": "我也问问", "reply_to": "m1"},
    )
    member_world = projector.apply(_world_with_lease(), member_event)

    assert scheduler.on_event(
        member_event, member_world, _persona(), now=120
    ) == ()
    assert scheduler.pending_window("885617919") is not None

    expired_scheduler = AttentionScheduler()
    expired_event = _event(
        "m3",
        actor_id="u1",
        occurred_at=300,
        payload={"text": "还在吗", "reply_to": "m1"},
    )
    expired_world = projector.apply(
        _world_with_lease(expires_at=280), expired_event
    )

    assert expired_scheduler.on_event(
        expired_event, expired_world, _persona(), now=300
    ) == ()
    assert expired_scheduler.pending_window("885617919") is not None


def test_live_lease_respects_other_member_address_without_blocking_direct_calls():
    for direction, expected_lane in (
        ({"reply_to_actor_id": "u2"}, "AMBIENT"),
        ({"mentions": ["u2"]}, "AMBIENT"),
        ({"reply_to_actor_id": "u2", "mentions": ["u2"]}, "AMBIENT"),
        ({}, "CONTINUATION"),
        ({"mentions": ["", "all", "@all", "0", "bot"]}, "CONTINUATION"),
        ({"reply_to_actor_id": "u2", "direct_address": True}, "FAST"),
        ({"mentions": ["bot", "u2"], "mentions_bot": True}, "FAST"),
        ({"reply_to_actor_id": "bot", "reply_to_bot": True}, "FAST"),
    ):
        event = _event("m2", occurred_at=120, payload={
            "text": "对", "bot_id": "bot", **direction,
        })
        world = GroupWorldProjector().apply(_world_with_lease(), event)
        scheduler = AttentionScheduler()
        frames = scheduler.on_event(event, world, _persona(), now=120)
        if expected_lane == "AMBIENT":
            assert frames == (), direction
            frames = scheduler.flush_due(now=122)
        else:
            assert scheduler.pending_window(event.group_id) is None
        assert frames[0].trigger_kind == expected_lane, direction
        assert world.conversation_lease.remaining_turns == 5


def test_boundary_and_capability_results_never_wait_for_ambient_window():
    scheduler = AttentionScheduler()
    boundary = _event("b1", event_type="safety.boundary", payload={"kind": "abuse"})
    capability = _event(
        "c1", event_type="capability.result", payload={"request_id": "tool-1"}
    )

    boundary_frame = scheduler.on_event(
        boundary, _world_with(boundary), _persona(), now=100
    )[0]
    capability_frame = scheduler.on_event(
        capability, _world_with(capability), _persona(), now=101
    )[0]

    assert boundary_frame.trigger_kind == "FAST"
    assert boundary_frame.urgency == "critical"
    assert capability_frame.trigger_kind == "FAST"
    assert capability_frame.deadline == 101


def test_overdue_commitment_only_creates_temporal_revalidation_candidate():
    event = _event(
        "due-1",
        event_type="temporal.commitment_due",
        payload={"commitment_id": "commit-1", "due_at": 90},
    )
    frame = AttentionScheduler().on_event(
        event, _world_with(event), _persona(), now=100
    )[0]

    assert frame.trigger_kind == "TEMPORAL"
    assert frame.requested_workers == ("commitment_revalidator",)
    assert frame.deadline == 100
    assert not hasattr(frame, "authorized_action")


def test_malformed_autonomous_temporal_payload_fails_closed():
    event = _event(
        "opportunity-bad",
        event_type="temporal.opportunity_due",
        payload={
            "source_event_ids": ["qq:source-1"],
            "audience": ["u1"],
            "earliest_at": "not-an-integer",
            "expires_at": 150,
            "attempt": 1,
            "followup_count": 0,
            "kind": "delayed-scene",
        },
    )

    frames = AttentionScheduler().on_event(
        event, _world_with(event), _persona(), now=100
    )

    assert frames == ()


def test_recursive_or_unknown_autonomous_temporal_source_fails_closed():
    scheduler = AttentionScheduler()
    recursive = _event(
        "opportunity-recursive",
        event_type="temporal.opportunity_due",
        payload={
            "source_event_ids": ["autonomy:opportunity:prior:1"],
            "audience": ["u1"],
            "earliest_at": 90,
            "expires_at": 150,
            "attempt": 1,
            "followup_count": 0,
            "kind": "delayed-scene",
        },
    )
    unknown = _event(
        "opportunity-unknown",
        event_type="temporal.opportunity_due",
        payload={
            "source_event_ids": ["qq:source-1"],
            "audience": ["u1"],
            "earliest_at": 90,
            "expires_at": 150,
            "attempt": 1,
            "followup_count": 0,
            "kind": "presence-ping",
        },
    )

    assert scheduler.on_event(
        recursive, _world_with(recursive), _persona(), now=100
    ) == ()
    assert scheduler.on_event(
        unknown, _world_with(unknown), _persona(), now=100
    ) == ()
