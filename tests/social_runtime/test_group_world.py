from __future__ import annotations

from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.world import GroupWorldProjector
from tests.factories import social_event_values


def _message(message_id, text, sender, reply_to=None, suggested_topic_id=None):
    payload = {"text": text}
    if reply_to is not None:
        payload["reply_to"] = reply_to
    if suggested_topic_id is not None:
        payload["suggested_topic_id"] = suggested_topic_id
    return SocialEventEnvelope.create(
        **social_event_values(
            event_id=f"qq:{message_id}",
            source_message_id=message_id,
            actor_id=sender,
            occurred_at=int(message_id[1:]),
            received_at=int(message_id[1:]),
            correlation_id=f"corr:{message_id}",
            payload=payload,
        )
    )


def _lease_event(
    event_id,
    event_type,
    *,
    target_id="u1",
    topic_id="m1",
    source_plan_id=None,
    occurred_at=100,
    expires_at=280,
    remaining_turns=5,
):
    return SocialEventEnvelope.create(
        **social_event_values(
            event_id=f"lease:{event_id}",
            event_type=event_type,
            source_message_id=None,
            actor_id=None,
            occurred_at=occurred_at,
            received_at=occurred_at,
            correlation_id=f"corr:lease:{event_id}",
            payload={
                "target_id": target_id,
                "topic_id": topic_id,
                "source_plan_id": source_plan_id or f"reply:{event_id}",
                "opened_at": occurred_at,
                "expires_at": expires_at,
                "remaining_turns": remaining_turns,
            },
        )
    )


def test_reply_chain_keeps_parallel_topics():
    projector = GroupWorldProjector()
    state = projector.empty("885617919")
    for event in (
        _message("m1", "项目怎么样", "u1"),
        _message("m2", "今晚吃啥", "u2"),
        _message("m3", "做到一半", "u3", reply_to="m1"),
    ):
        state = projector.apply(state, event)

    assert len(state.active_topics) == 2
    assert state.topic_for_message("m3").root_event_id == "m1"
    assert state.scene_version == 3


def test_explicit_reply_fact_overrides_model_topic_suggestion():
    projector = GroupWorldProjector()
    state = projector.empty("885617919")
    state = projector.apply(state, _message("m1", "项目怎么样", "u1"))
    state = projector.apply(state, _message("m2", "今晚吃啥", "u2"))

    state = projector.apply(
        state,
        _message(
            "m3",
            "做到一半",
            "u3",
            reply_to="m1",
            suggested_topic_id="m2",
        ),
    )

    assert state.topic_for_message("m3").topic_id == "m1"


def test_world_state_round_trips_without_losing_topic_ownership():
    projector = GroupWorldProjector()
    state = projector.apply(
        projector.empty("885617919"),
        _message("m1", "早", "u1"),
    )

    recovered = projector.from_dict(projector.to_dict(state))

    assert recovered == state
    assert recovered.topic_for_message("m1").participant_ids == ("u1",)


def test_conversation_lease_opens_and_advances_without_resetting_turn_budget():
    projector = GroupWorldProjector()
    state = projector.empty("885617919")

    state = projector.apply(
        state,
        _lease_event("open", "conversation.lease_opened", remaining_turns=5),
    )
    state = projector.apply(
        state,
        _lease_event(
            "next",
            "conversation.lease_advanced",
            occurred_at=120,
            expires_at=300,
            remaining_turns=4,
        ),
    )

    assert state.conversation_lease is not None
    assert state.conversation_lease.target_id == "u1"
    assert state.conversation_lease.topic_id == "m1"
    assert state.conversation_lease.source_plan_id == "reply:next"
    assert state.conversation_lease.expires_at == 300
    assert state.conversation_lease.remaining_turns == 4


def test_mismatched_conversation_lease_advance_is_ignored():
    projector = GroupWorldProjector()
    state = projector.apply(
        projector.empty("885617919"),
        _lease_event("open", "conversation.lease_opened"),
    )

    unchanged = projector.apply(
        state,
        _lease_event(
            "wrong",
            "conversation.lease_advanced",
            target_id="u2",
            remaining_turns=4,
        ),
    )

    assert unchanged.conversation_lease == state.conversation_lease


def test_old_world_snapshot_restores_with_no_conversation_lease():
    projector = GroupWorldProjector()
    payload = projector.to_dict(projector.empty("885617919"))
    payload.pop("conversation_lease")

    restored = projector.from_dict(payload)

    assert restored.conversation_lease is None
