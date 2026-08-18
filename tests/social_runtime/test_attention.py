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
