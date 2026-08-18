from __future__ import annotations

from groupmate.social_runtime.society.culture import CultureProjector


def test_culture_requires_three_independent_events_or_admin_confirmation():
    projector = CultureProjector()
    artifact = projector.empty("aemeath", "g1", "cookie_recycling")
    first = projector.observe(artifact, "e1", now=100)
    second = projector.observe(first, "e2", now=110)
    active = projector.observe(second, "e3", now=120)
    confirmed = projector.confirm_by_admin(
        projector.empty("aemeath", "g1", "night_greeting"),
        admin_id="admin:1",
        now=120,
    )

    assert first.status == second.status == "candidate"
    assert active.status == "active"
    assert confirmed.status == "active"


def test_active_culture_decays_after_thirty_days_without_evidence():
    projector = CultureProjector()
    artifact = projector.empty("aemeath", "g1", "ritual")
    for index in range(3):
        artifact = projector.observe(artifact, f"e{index}", now=100 + index)
    decayed = projector.decay(artifact, now=102 + 30 * 24 * 60 * 60 + 1)

    assert artifact.status == "active"
    assert decayed.status == "candidate"
