"""五档好感领域模型：边界、初始关系与回应姿态。"""

from dataclasses import asdict

import pytest

from groupmate.models import RelationshipState
from groupmate.social.affinity import (
    AffinityBand,
    ResponsePosture,
    band_for_affinity,
    clamp_affinity,
    initial_affinity_for_relationship,
    snapshot_for_relationship,
)


@pytest.mark.parametrize(
    "score, expected",
    (
        (-100, AffinityBand.HOSTILE),
        (-50, AffinityBand.HOSTILE),
        (-49, AffinityBand.WARY),
        (-1, AffinityBand.WARY),
        (0, AffinityBand.NEUTRAL),
        (29, AffinityBand.NEUTRAL),
        (30, AffinityBand.FRIENDLY),
        (69, AffinityBand.FRIENDLY),
        (70, AffinityBand.CLOSE),
        (100, AffinityBand.CLOSE),
    ),
)
def test_affinity_band_boundaries(score, expected):
    assert band_for_affinity(score) is expected


def test_affinity_is_clamped_to_domain_range():
    assert clamp_affinity(-101) == -100
    assert clamp_affinity(101) == 100


@pytest.mark.parametrize(
    "relationship, expected",
    (
        ("", 0),
        ("普通群友", 0),
        ("闺蜜", 50),
        ("最亲近", 80),
        ("未知关系", 0),
    ),
)
def test_configured_relationship_only_seeds_missing_state(relationship, expected):
    assert initial_affinity_for_relationship(relationship) == expected


def test_snapshot_exposes_only_discrete_behavior_inputs():
    state = RelationshipState(group_id="g1", user_id="u1", affinity=75)

    snapshot = snapshot_for_relationship(state, configured_relationship="普通群友")

    assert snapshot.band is AffinityBand.CLOSE
    assert snapshot.response_posture is ResponsePosture.CLOSE
    assert asdict(snapshot) == {
        "band": AffinityBand.CLOSE,
        "response_posture": ResponsePosture.CLOSE,
    }
    assert not hasattr(snapshot, "score")
    assert not hasattr(snapshot, "affinity")


def test_missing_state_uses_configured_relationship_seed():
    snapshot = snapshot_for_relationship(None, configured_relationship="闺蜜")

    assert snapshot.band is AffinityBand.FRIENDLY
    assert snapshot.response_posture is ResponsePosture.WARM


def test_boundary_pressure_overrides_close_posture():
    state = RelationshipState(
        group_id="g1",
        user_id="u1",
        affinity=90,
        boundary_pressure=20,
    )

    snapshot = snapshot_for_relationship(state, configured_relationship="最亲近")

    assert snapshot.band is AffinityBand.CLOSE
    assert snapshot.response_posture is ResponsePosture.FIRM
