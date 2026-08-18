from __future__ import annotations

import pytest

from groupmate.social_runtime.contracts import (
    ActorCursor,
    GlobalStateEffect,
    PersonaSnapshot,
    RuntimeMode,
    SocialEventEnvelope,
)
from tests.factories import social_event_values


def test_event_payload_is_immutable_from_its_source_mapping():
    source = {"text": "早"}
    event = SocialEventEnvelope.create(**social_event_values(payload=source))

    source["text"] = "被修改"

    assert event.payload["text"] == "早"


@pytest.mark.parametrize("field", ("event_id", "event_type", "persona_id", "correlation_id"))
def test_event_rejects_empty_required_identity(field):
    with pytest.raises(ValueError, match=f"{field} must not be empty"):
        SocialEventEnvelope.create(**social_event_values(**{field: "  "}))


@pytest.mark.parametrize("field", ("occurred_at", "received_at"))
def test_event_rejects_negative_timestamps(field):
    with pytest.raises(ValueError, match=f"{field} must not be negative"):
        SocialEventEnvelope.create(**social_event_values(**{field: -1}))


def test_event_rejects_payload_that_cannot_be_serialized():
    with pytest.raises(ValueError, match="payload must be JSON serializable"):
        SocialEventEnvelope.create(**social_event_values(payload={"bad": object()}))


def test_event_round_trip_preserves_correlation_and_causation():
    event = SocialEventEnvelope.create(
        **social_event_values(correlation_id="corr-7", causation_id="evt-parent")
    )

    restored = SocialEventEnvelope.from_dict(event.to_dict())

    assert restored == event
    assert restored.correlation_id == "corr-7"
    assert restored.causation_id == "evt-parent"


def test_runtime_modes_exclude_legacy_execution():
    assert tuple(RuntimeMode) == (
        RuntimeMode.OFF,
        RuntimeMode.SHADOW,
        RuntimeMode.SOCIAL_RUNTIME,
    )
    with pytest.raises(ValueError):
        RuntimeMode("LEGACY")


def test_versioned_contracts_keep_evidence_and_expected_versions():
    cursor = ActorCursor("persona:aemeath", last_sequence=8, version=3)
    persona = PersonaSnapshot(
        persona_id="aemeath",
        state_version=3,
        config_version=2,
        presence="awake",
        energy=90,
        mode="social",
        modifiers=("warm",),
    )
    effect = GlobalStateEffect(
        effect_id="fx-1",
        source_event_id="evt-1",
        expected_version=3,
        kind="energy_delta",
        amount=-5,
        evidence_event_ids=("evt-1",),
    )

    assert cursor.last_sequence == 8
    assert persona.config_version == 2
    assert effect.evidence_event_ids == ("evt-1",)
