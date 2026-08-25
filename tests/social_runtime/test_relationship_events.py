from __future__ import annotations

import dataclasses

import pytest

from groupmate.social_runtime.persistence.repositories import (
    RelationshipEventIdentityConflict,
    SQLiteSocietyRepository,
)
from groupmate.social_runtime.society.relationship_events import (
    RelationshipEventService,
    RelationshipEventPolicy,
    RelationshipEventProposal,
)
from groupmate.social_runtime.society.relationships import RelationshipProjection


def _proposal(**overrides):
    values = {
        "event_id": "relationship:r1",
        "persona_id": "p",
        "group_id": "g",
        "subject_id": "u",
        "kind": "warm_exchange",
        "confidence": 0.9,
        "severity": "ordinary",
        "summary": "成员认真表达了感谢",
        "source_event_ids": ("message:e1",),
        "occurred_at": 100,
        "repair_of": None,
        "sensitivity": "normal",
    }
    values.update(overrides)
    return RelationshipEventProposal(**values)


def test_model_proposal_never_carries_a_numeric_delta():
    fields = {item.name for item in dataclasses.fields(RelationshipEventProposal)}

    assert "amount" not in fields


def test_warm_exchange_maps_to_a_small_local_delta():
    decision = RelationshipEventPolicy().decide(
        _proposal(),
        current=RelationshipProjection("p", "g", "u"),
        positive_delta_today=0.0,
    )

    assert decision.outcome == "ACCEPT"
    assert decision.evidence is not None
    assert decision.evidence.kind == "warm_exchange"
    assert decision.evidence.amount == 2
    assert decision.public_delta == 0.4


def test_low_confidence_boundary_event_is_rejected():
    decision = RelationshipEventPolicy().decide(
        _proposal(
            kind="boundary_pressure",
            confidence=0.82,
            severity="severe",
        ),
        current=RelationshipProjection("p", "g", "u"),
        positive_delta_today=0.0,
    )

    assert decision.outcome == "REJECT"
    assert decision.reason_codes == ("confidence_below_threshold",)
    assert decision.evidence is None


def test_positive_daily_budget_rejects_an_event_that_would_exceed_limit():
    decision = RelationshipEventPolicy().decide(
        _proposal(),
        current=RelationshipProjection("p", "g", "u"),
        positive_delta_today=0.6,
    )

    assert decision.outcome == "REJECT"
    assert decision.reason_codes == ("positive_daily_budget_exhausted",)


def test_repair_confirmed_reduces_boundary_pressure_locally():
    decision = RelationshipEventPolicy().decide(
        _proposal(
            kind="repair_confirmed",
            confidence=0.92,
            severity="significant",
            repair_of="relationship:old",
        ),
        current=RelationshipProjection("p", "g", "u", boundary_pressure=10),
        positive_delta_today=0.0,
    )

    assert decision.outcome == "ACCEPT"
    assert decision.evidence is not None
    assert decision.evidence.kind == "boundary_pressure"
    assert decision.evidence.amount == -4


def _service(tmp_path):
    return RelationshipEventService(
        SQLiteSocietyRepository(tmp_path / "runtime.db")
    )


def test_same_relationship_event_is_applied_once(tmp_path):
    service = _service(tmp_path)

    first = service.process(_proposal(event_id="r1"), mode="SOCIAL_RUNTIME")
    second = service.process(_proposal(event_id="r1"), mode="SOCIAL_RUNTIME")

    assert first.outcome == "ACCEPT"
    assert second.outcome == "DUPLICATE"
    assert service.snapshot("p", "g", "u").version == 1


def test_shadow_records_suggestion_without_changing_projection(tmp_path):
    service = _service(tmp_path)

    decision = service.process(_proposal(event_id="r2"), mode="SHADOW")

    assert decision.outcome == "SUGGEST"
    assert service.snapshot("p", "g", "u").version == 0
    assert service.decisions("p", "g", "u")[0].outcome == "SUGGEST"


def test_same_event_id_with_different_content_is_rejected(tmp_path):
    service = _service(tmp_path)
    service.process(_proposal(event_id="r3"), mode="SOCIAL_RUNTIME")

    with pytest.raises(RelationshipEventIdentityConflict):
        service.process(
            _proposal(event_id="r3", summary="不同的关系事实"),
            mode="SOCIAL_RUNTIME",
        )


def test_daily_positive_budget_is_based_on_committed_events(tmp_path):
    service = _service(tmp_path)

    first = service.process(_proposal(event_id="r4"), mode="SOCIAL_RUNTIME")
    second = service.process(_proposal(event_id="r5"), mode="SOCIAL_RUNTIME")
    third = service.process(_proposal(event_id="r6"), mode="SOCIAL_RUNTIME")

    assert first.outcome == "ACCEPT"
    assert second.outcome == "ACCEPT"
    assert third.outcome == "REJECT"
    assert third.reason_codes == ("positive_daily_budget_exhausted",)
