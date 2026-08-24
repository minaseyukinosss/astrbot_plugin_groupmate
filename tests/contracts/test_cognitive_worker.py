from __future__ import annotations

import asyncio

import pytest

from groupmate.adapters.astrbot_models import AstrBotModelPort
from groupmate.social_runtime.attention import AttentionFrame
from groupmate.social_runtime.cognition.astrbot_workers import AstrBotStructuredWorker
from groupmate.social_runtime.cognition.contracts import (
    CognitiveContext,
    CognitiveObservation,
)
from groupmate.social_runtime.cognition.service import CognitionBudget, CognitionService


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


def test_cognition_service_preserves_structured_model_failure_code():
    class InvalidModel:
        async def complete_json(self, *, schema, payload):
            return {"observations": "not-a-list"}

    worker = AstrBotStructuredWorker("scene_interpreter", InvalidModel())
    service = CognitionService(
        workers={"scene_interpreter": worker},
        budget=CognitionBudget(max_worker_calls=1, max_cost_units=1),
    )

    snapshot = asyncio.run(service.evaluate(_frame(), _context()))

    diagnostic = snapshot.worker_diagnostics[-1]
    assert diagnostic.worker == "scene_interpreter"
    assert diagnostic.status == "INVALID_OUTPUT"
    assert diagnostic.diagnostic_code == "invalid_worker_output"
    assert snapshot.degraded is True


def test_astrbot_model_port_uses_provider_and_worker_identity():
    class Response:
        completion_text = "```json\n{\"observations\": []}\n```"

    class Context:
        def __init__(self):
            self.calls = []

        async def llm_generate(self, **kwargs):
            self.calls.append(kwargs)
            return Response()

    async def scenario():
        context = Context()
        model = AstrBotModelPort(context, "provider:text")
        worker = AstrBotStructuredWorker("scene_interpreter", model)
        result = await worker.observe(_frame(), _context())
        return context, result

    context, result = asyncio.run(scenario())

    assert result == ()
    assert context.calls[0]["chat_provider_id"] == "provider:text"
    assert '"worker": "scene_interpreter"' in context.calls[0]["prompt"]
    assert "判断当前话题的社交信号" in context.calls[0]["prompt"]


def test_missing_requested_worker_marks_cognition_degraded():
    service = CognitionService(
        workers={}, budget=CognitionBudget(max_worker_calls=2, max_cost_units=2)
    )

    snapshot = asyncio.run(service.evaluate(_frame(), _context()))

    assert snapshot.degraded is True
    assert "worker_missing:scene_interpreter" in snapshot.diagnostics


def test_requested_model_workers_start_concurrently_and_merge_in_frame_order():
    class BarrierWorker:
        def __init__(self, name, entered, both_entered, release):
            self.name = name
            self.entered = entered
            self.both_entered = both_entered
            self.release = release

        async def observe(self, frame, context):
            self.entered.set()
            if all(event.is_set() for event in entered_events):
                self.both_entered.set()
            await self.release.wait()
            return (
                CognitiveObservation.create(
                    worker=self.name,
                    kind=f"fact.{self.name}",
                    proposition={"value": self.name},
                    confidence=1.0,
                    evidence_event_ids=(frame.focus_event_ids[0],),
                    scene_version=frame.scene_version,
                    expires_at=context.now + 30,
                    uncertainty=(),
                ),
            )

    async def scenario():
        first = asyncio.Event()
        second = asyncio.Event()
        both_entered = asyncio.Event()
        release = asyncio.Event()
        entered_events.extend((first, second))
        workers = {
            name: BarrierWorker(name, entered, both_entered, release)
            for name, entered in (
                ("scene_interpreter", first),
                ("participation_assessor", second),
            )
        }
        frame = AttentionFrame(
            **{
                **_frame().__dict__,
                "trigger_kind": "AMBIENT",
                "requested_workers": tuple(workers),
            }
        )
        service = CognitionService(
            workers=workers,
            budget=CognitionBudget(max_worker_calls=2, max_cost_units=2),
        )
        task = asyncio.create_task(service.evaluate(frame, _context()))
        try:
            await asyncio.wait_for(both_entered.wait(), timeout=0.2)
        except TimeoutError:
            release.set()
            await task
            raise
        release.set()
        return await task

    entered_events = []
    snapshot = asyncio.run(scenario())

    assert [item.worker for item in snapshot.worker_diagnostics[1:]] == [
        "scene_interpreter",
        "participation_assessor",
    ]
    assert all(
        item.status == "SUCCEEDED"
        for item in snapshot.worker_diagnostics
    )
    assert [entry.observation.worker for entry in snapshot.entries[1:]] == [
        "scene_interpreter",
        "participation_assessor",
    ]
