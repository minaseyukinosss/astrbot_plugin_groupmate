from __future__ import annotations

import pytest

from groupmate.social_runtime.persona.modes import (
    InvalidModeCombination,
    ModeDirector,
    ModeSignal,
    PersonaModeState,
)


def test_focused_task_and_drowsy_is_legal_but_boundary_playful_is_not():
    state = PersonaModeState(
        primary="focused_task",
        modifiers=("drowsy",),
        activated_by=("task:1", "time:late"),
        expires_at=None,
    )

    assert state.primary == "focused_task"
    with pytest.raises(InvalidModeCombination, match="boundary.*playful"):
        PersonaModeState(
            primary="boundary",
            modifiers=("playful",),
            activated_by=("boundary:1",),
            expires_at=None,
        )


def test_mode_transitions_are_event_driven_and_reason_chain_survives_round_trip():
    director = ModeDirector()
    state = PersonaModeState.social()
    focused = director.transition(
        state,
        ModeSignal(kind="task.accepted", source_id="task:1", occurred_at=100),
    )
    drowsy = director.transition(
        focused,
        ModeSignal(kind="time.drowsy", source_id="clock:late", occurred_at=120),
    )

    recovered = director.from_dict(director.to_dict(drowsy))

    assert recovered.primary == "focused_task"
    assert recovered.modifiers == ("drowsy",)
    assert recovered.activated_by == ("task:1", "clock:late")
    assert recovered == drowsy


def test_unknown_or_model_invented_mode_signal_is_rejected():
    with pytest.raises(ValueError, match="unsupported mode signal"):
        ModeDirector().transition(
            PersonaModeState.social(),
            ModeSignal(kind="model.random_mood", source_id="llm:1", occurred_at=100),
        )
