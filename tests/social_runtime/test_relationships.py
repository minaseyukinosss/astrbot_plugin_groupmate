from __future__ import annotations

from groupmate.social_runtime.persistence.repositories import SQLiteSocietyRepository
from groupmate.social_runtime.society.relationships import (
    RelationshipEvidence,
    RelationshipProjector,
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
