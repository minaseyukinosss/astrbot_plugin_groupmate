from __future__ import annotations

import asyncio

import pytest

from groupmate.social_runtime.attention import AttentionFrame
from groupmate.social_runtime.cognition.astrbot_workers import AstrBotStructuredWorker
from groupmate.social_runtime.cognition.contracts import (
    CognitiveContext,
    CognitiveObservation,
)


def _context():
    return CognitiveContext.create(
        group_id="885617919",
        scene_version=2,
        persona_state_version=3,
        config_version=4,
        now=100,
        focus_events=({"event_id": "qq:m1", "text": "早"},),
        world_summary={"topics": ["m1"]},
        constraints=("shadow_only",),
        token_budget=800,
    )


def _frame():
    return AttentionFrame(
        frame_id="attention:1",
        group_id="885617919",
        scene_version=2,
        trigger_kind="FAST",
        focus_topic_ids=("m1",),
        focus_event_ids=("qq:m1",),
        candidate_audiences=("u1",),
        urgency="high",
        deadline=100,
        requested_workers=("scene_interpreter",),
        persona_state_version=3,
        config_version=4,
    )


def test_observation_requires_evidence_and_bounded_confidence():
    with pytest.raises(ValueError, match="evidence"):
        CognitiveObservation.create(
            worker="scene_interpreter",
            kind="social_signal",
            proposition={"value": "greeting"},
            confidence=0.8,
            evidence_event_ids=(),
            scene_version=2,
            expires_at=120,
            uncertainty=(),
        )

    with pytest.raises(ValueError, match="confidence"):
        CognitiveObservation.create(
            worker="scene_interpreter",
            kind="social_signal",
            proposition={"value": "greeting"},
            confidence=1.2,
            evidence_event_ids=("qq:m1",),
            scene_version=2,
            expires_at=120,
            uncertainty=(),
        )


def test_cognitive_context_is_json_bounded_and_immutable():
    context = _context()

    with pytest.raises(TypeError):
        context.world_summary["topics"] = []
    assert context.token_budget == 800


def test_astrbot_worker_invalid_structured_output_returns_empty_with_error_code():
    class InvalidModel:
        async def complete_json(self, *, schema, payload):
            assert schema["type"] == "object"
            assert payload["frame"]["frame_id"] == "attention:1"
            return {"observations": "not-a-list"}

    diagnostics = []
    worker = AstrBotStructuredWorker(
        "scene_interpreter",
        InvalidModel(),
        diagnostic_sink=diagnostics.append,
    )

    result = asyncio.run(worker.observe(_frame(), _context()))

    assert result == ()
    assert diagnostics == ["invalid_worker_output"]
