from __future__ import annotations

import pytest

from groupmate.social_runtime.persistence.repositories import SQLiteSocietyRepository
from groupmate.social_runtime.society.relationships import (
    PublicAffection,
    RelationshipEvidence,
    RelationshipProjector,
    RelationshipProjection,
    RelationshipStage,
    relationship_stage,
)


def test_relationship_dimensions_are_clamped_and_never_grant_capability_permission():
    projector = RelationshipProjector()
    state = projector.empty("aemeath", "g1", "u1")
    state = projector.apply(
        state, RelationshipEvidence("e1", "trust_confirmed", 500, 100)
    )

    assert state.trust == 100
    assert state.version == 1
    assert projector.authorizes_capability(state, "send_mail") is False


def test_same_member_relationship_is_isolated_between_groups(tmp_path):
    repository = SQLiteSocietyRepository(tmp_path / "groupmate-social-runtime-v2.db")
    projector = RelationshipProjector()
    g1 = projector.apply(
        projector.empty("aemeath", "g1", "u1"),
        RelationshipEvidence("e1", "warm_exchange", 20, 100),
    )
    repository.save_relationship(g1)

    assert repository.load_relationship("aemeath", "g1", "u1").warmth == 20
    assert repository.load_relationship("aemeath", "g2", "u1").warmth == 0


def test_public_affection_score_is_derived_from_dimensions():
    state = RelationshipProjection(
        "aemeath",
        "g1",
        "u1",
        familiarity=20,
        warmth=30,
        trust=10,
        reciprocity=10,
        play_acceptance=5,
        reliability=20,
        care_permission=10,
        boundary_pressure=0,
    )

    score = PublicAffection.from_projection(state)

    assert score.value == 15.9
    assert score.stage is RelationshipStage.KNOWS


@pytest.mark.parametrize(
    ("value", "stage"),
    (
        (-40.0, "警戒"),
        (-10.0, "疏远"),
        (0.0, "陌生"),
        (10.0, "认识"),
        (30.0, "熟悉"),
        (55.0, "亲近"),
        (80.0, "默契"),
    ),
)
def test_stage_boundaries_have_one_owner(value, stage):
    assert relationship_stage(value).value == stage


def test_repository_returns_projection_and_derived_affection(tmp_path):
    repository = SQLiteSocietyRepository(tmp_path / "runtime.db")

    state, affection = repository.relationship_snapshot("aemeath", "g1", "u1")

    assert state.version == 0
    assert affection == PublicAffection(0.0, RelationshipStage.STRANGER)
