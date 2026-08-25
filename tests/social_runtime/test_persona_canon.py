from __future__ import annotations

import pytest

from groupmate.social_runtime.persona.canon import PersonaCanon, PersonaFact
from groupmate.social_runtime.persona.presets import AEMEATH_CURRENT_CANON


def test_current_snapshot_does_not_promote_expired_state():
    canon = PersonaCanon(
        current_phase=4,
        checked_at=100,
        facts=(
            PersonaFact(
                "ghost",
                "history",
                "曾以电子幽灵存在",
                "3.1",
                1,
                3,
                ("电子",),
            ),
            PersonaFact(
                "body",
                "current_state",
                "已重归现世并拥有躯壳",
                "3.3",
                4,
                None,
                ("身体", "现世"),
            ),
        ),
    )

    snapshot = canon.current_snapshot()

    assert [item.fact_id for item in snapshot.current_state] == ["body"]
    assert [item.fact_id for item in snapshot.history] == ["ghost"]


def test_aemeath_current_state_is_embodied_and_ghost_is_history():
    snapshot = AEMEATH_CURRENT_CANON.current_snapshot()

    assert any("重归现世" in item.text for item in snapshot.current_state)
    assert not any("电子幽灵" in item.text for item in snapshot.current_state)
    assert any("电子幽灵" in item.text for item in snapshot.history)


def test_canon_round_trip_preserves_fact_time_bounds():
    restored = PersonaCanon.from_mapping(AEMEATH_CURRENT_CANON.to_mapping())

    assert restored == AEMEATH_CURRENT_CANON


def test_canon_rejects_unknown_fact_categories():
    with pytest.raises(ValueError, match="category"):
        PersonaFact("bad", "mood", "未知类别", "x", 1, None)
