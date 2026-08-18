from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from groupmate.social_runtime.attention import AttentionFrame
from groupmate.social_runtime.cognition.blackboard import (
    CognitionBlackboard,
    ObservationRejected,
)
from groupmate.social_runtime.cognition.contracts import (
    CognitiveContext,
    CognitiveObservation,
)
from groupmate.social_runtime.cognition.service import (
    CognitionBudget,
    CognitionService,
)


def _frame(trigger_kind="AMBIENT", requested_workers=("w1", "w2")):
    return AttentionFrame(
        frame_id="attention:1",
        group_id="885617919",
        scene_version=5,
        trigger_kind=trigger_kind,
        focus_topic_ids=("m1", "m2"),
        focus_event_ids=("qq:m1", "qq:m2"),
        candidate_audiences=("u1", "u2"),
        urgency="normal",
        deadline=100,
        requested_workers=requested_workers,
        persona_state_version=2,
        config_version=3,
    )


def _observation(value, *, scene_version=5, expires_at=120, worker="w1"):
    return CognitiveObservation.create(
        worker=worker,
        kind="social_signal",
        proposition={
            "subject_id": "u1",
            "attribute": "interaction_intent",
            "value": value,
        },
        confidence=0.7,
        evidence_event_ids=("qq:m1",),
        scene_version=scene_version,
        expires_at=expires_at,
        uncertainty=("tone_ambiguous",),
    )


def _context():
    return CognitiveContext.create(
        group_id="885617919",
        scene_version=5,
        persona_state_version=2,
        config_version=3,
        now=100,
        focus_events=(
            {"event_id": "qq:m1", "text": "你认真的？"},
            {"event_id": "qq:m2", "text": "哈哈"},
        ),
        world_summary={"topic_count": 2},
        constraints=("shadow_only",),
        token_budget=1000,
    )


def test_blackboard_rejects_wrong_scene_and_expired_observations():
    board = CognitionBlackboard(_frame(), now=100)

    with pytest.raises(ObservationRejected, match="scene_version"):
        board.add(_observation("joke", scene_version=4))
    with pytest.raises(ObservationRejected, match="expired"):
        board.add(_observation("joke", expires_at=100))


def test_conflicting_propositions_are_preserved_and_marked():
    board = CognitionBlackboard(_frame(), now=100)
    board.add(_observation("joke", worker="w1"))
    board.add(_observation("serious", worker="w2"))

    snapshot = board.snapshot(cost_level=2)

    assert [entry.observation.proposition["value"] for entry in snapshot.entries] == [
        "joke",
        "serious",
    ]
    assert [entry.conflict for entry in snapshot.entries] == [True, True]
    assert snapshot.conflict_count == 1


def test_budget_exhaustion_still_runs_level_zero_and_degrades_to_observe():
    class RuleWorker:
        name = "level0.rules"

        def __init__(self):
            self.calls = 0

        async def observe(self, frame, context):
            self.calls += 1
            return (
                CognitiveObservation.create(
                    worker=self.name,
                    kind="fact.target",
                    proposition={"subject_id": "u1", "value": "direct"},
                    confidence=1.0,
                    evidence_event_ids=("qq:m1",),
                    scene_version=frame.scene_version,
                    expires_at=120,
                    uncertainty=(),
                ),
            )

    class ModelWorker:
        name = "w1"

        def __init__(self):
            self.calls = 0

        async def observe(self, frame, context):
            self.calls += 1
            return (_observation("joke"),)

    rules = RuleWorker()
    model = ModelWorker()
    service = CognitionService(
        rule_worker=rules,
        workers={"w1": model},
        budget=CognitionBudget(max_worker_calls=0, max_cost_units=0),
    )

    snapshot = asyncio.run(service.evaluate(_frame(requested_workers=("w1",)), _context()))

    assert rules.calls == 1
    assert model.calls == 0
    assert snapshot.degraded is True
    assert snapshot.recommended_outcome == "OBSERVE"
    assert snapshot.entries[0].observation.kind == "fact.target"


def test_level_two_runs_multiple_requested_workers_within_budget():
    class Worker:
        def __init__(self, name, value):
            self.name = name
            self.value = value
            self.calls = 0

        async def observe(self, frame, context):
            self.calls += 1
            return (_observation(self.value, worker=self.name),)

    first = Worker("w1", "joke")
    second = Worker("w2", "serious")
    service = CognitionService(
        workers={"w1": first, "w2": second},
        budget=CognitionBudget(max_worker_calls=2, max_cost_units=2),
    )

    snapshot = asyncio.run(service.evaluate(_frame(), _context()))

    assert snapshot.cost_level == 2
    assert first.calls == second.calls == 1
    assert snapshot.degraded is False
    assert snapshot.conflict_count == 1


def test_level_three_adds_critic_without_skipping_hard_rules():
    class Worker:
        def __init__(self, name):
            self.name = name
            self.calls = 0

        async def observe(self, frame, context):
            self.calls += 1
            return (_observation("review", worker=self.name),)

    model = Worker("w1")
    critic = Worker("critic")
    service = CognitionService(
        workers={"w1": model},
        critic_worker=critic,
        budget=CognitionBudget(max_worker_calls=2, max_cost_units=3),
    )
    frame = replace(
        _frame(trigger_kind="FAST", requested_workers=("w1",)),
        urgency="critical",
    )

    snapshot = asyncio.run(service.evaluate(frame, _context()))

    assert snapshot.cost_level == 3
    assert model.calls == critic.calls == 1
    assert any(entry.observation.kind == "fact.safety" for entry in snapshot.entries)


def test_hanging_worker_times_out_and_degrades_to_observe():
    class HangingWorker:
        name = "w1"

        async def observe(self, frame, context):
            await asyncio.Event().wait()

    service = CognitionService(
        workers={"w1": HangingWorker()},
        budget=CognitionBudget(
            max_worker_calls=1,
            max_cost_units=1,
            worker_timeout_seconds=0.01,
        ),
    )

    snapshot = asyncio.run(
        service.evaluate(
            _frame(trigger_kind="FAST", requested_workers=("w1",)),
            _context(),
        )
    )

    assert snapshot.degraded is True
    assert snapshot.recommended_outcome == "OBSERVE"
    assert "worker_timeout:w1" in snapshot.diagnostics
