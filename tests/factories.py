"""Hand-authored Social Runtime test fixtures."""

from __future__ import annotations


def social_event_values(**overrides):
    values = {
        "event_id": "evt-1",
        "event_type": "platform.message",
        "occurred_at": 100,
        "received_at": 101,
        "persona_id": "aemeath",
        "group_id": "885617919",
        "actor_id": "323537051",
        "source_message_id": "m1",
        "correlation_id": "corr-1",
        "causation_id": None,
        "payload": {"text": "早"},
    }
    values.update(overrides)
    return values
