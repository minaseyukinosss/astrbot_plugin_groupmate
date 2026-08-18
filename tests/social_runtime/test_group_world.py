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
