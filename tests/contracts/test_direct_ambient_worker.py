from __future__ import annotations

import asyncio
import json

import pytest

from groupmate.adapters.deepseek_cognition import (
    DeepSeekCognitionClient,
    DirectCognitionError,
    DirectCognitionResponse,
)
from groupmate.social_runtime.attention import AttentionFrame
from groupmate.social_runtime.cognition.ambient_worker import DirectAmbientWorker
from groupmate.social_runtime.cognition.contracts import CognitiveContext
from groupmate.social_runtime.cognition.service import CognitionBudget, CognitionService


class FakeClient:
    def __init__(self, verdict=None, error=None):
        self.verdict = verdict
        self.error = error
        self.facts = None

    def input_bytes(self, facts):
        return DeepSeekCognitionClient(
            api_key="test",
            api_base="https://api.deepseek.com",
            model="deepseek-v4-flash",
            transport=self,
        ).input_bytes(facts)

    async def classify(self, facts):
        self.facts = facts
        if self.error is not None:
            raise self.error
        return DirectCognitionResponse(
            verdict=self.verdict,
            latency_ms=23,
            request_bytes=self.input_bytes(facts),
            backend="direct_deepseek",
            model="deepseek-v4-flash",
        )


def _frame():
    return AttentionFrame(
        frame_id="attention:ambient",
        group_id="885617919",
        scene_version=3,
        trigger_kind="AMBIENT",
        focus_topic_ids=("topic-1", "topic-2"),
        focus_event_ids=tuple(f"qq:{index}" for index in range(2, 14)),
        candidate_audiences=("u1", "u2"),
        urgency="normal",
        deadline=108,
        requested_workers=("ambient_social_assessor",),
        persona_state_version=4,
        config_version=5,
    )


def _context():
    events = []
    for index in range(14):
        events.append(
            {
                "event_id": f"qq:{index}",
                "actor_id": "u1" if index % 2 else "u2",
                "scene_version": 999,
                "payload": {
                    "text": "这是一段群聊文本" * 80,
                    "sender": {"id": "u1", "name": "夏夏"},
                    "reply_to": "previous-message",
                    "reply_to_actor_id": "u2",
                    "mentions": ["u2"],
                    "segments": [
                        {"type": "image", "data": {"url": "https://secret/media.jpg"}},
                        {"type": "at", "data": {"qq": "u2", "name": "阿明"}},
                    ],
                },
            }
        )
    return CognitiveContext.create(
        group_id="885617919",
        scene_version=3,
        persona_state_version=4,
        config_version=5,
        now=108,
        focus_events=tuple(events),
        world_summary={
            "topics": [
                {
                    "topic_id": f"topic-{index}",
                    "message_ids": ["private-message-id"] * 20,
                    "participant_ids": ["u1", "u2", "u3"],
                    "last_event_at": 100 + index,
                }
                for index in range(6)
            ],
            "audiences": [
                {"actor_id": f"u{index}", "message_count": index, "last_seen_at": 100}
                for index in range(1, 11)
            ],
            "group_activity": {"event_count": 20, "message_count": 18, "last_event_at": 107},
            "last_bot_event_at": 80,
            "conversation_lease": {
                "target_id": "u1",
                "topic_id": "topic-1",
                "source_plan_id": "private-plan",
                "opened_at": 70,
                "expires_at": 120,
                "remaining_turns": 1,
            },
            "persona_profile": {
                "identity": {"background": "private persona background"},
                "presence": {"default_mode": "social", "rhythm": "自然参与"},
                "participation": {
                    "initiative": "balanced",
                    "speak_when": "有帮助时",
                    "stay_silent_when": "打断别人时",
                },
                "expression": {"tone": "private style"},
            },
            "relationship_memories": [
                {
                    "event_id": "relationship:boundary-1",
                    "subject_id": "u1",
                    "kind": "boundary_pressure",
                    "summary": "成员上次明确辱骂爱弥斯",
                    "occurred_at": 90,
                }
            ],
            "database_path": "/private/runtime.db",
        },
        constraints=("no_side_effects", "evidence_required"),
        token_budget=1024,
    )


def _verdict(**overrides):
    value = {
        "decision": "speak",
        "signal": "help_request",
        "target_id": "u1",
        "evidence_event_ids": ["qq:12", "qq:13"],
        "confidence": 0.86,
        "disruption": 0.1,
        "novelty": 0.82,
        "reason": "成员提出了可以直接帮助的问题",
    }
    value.update(overrides)
    return value


def test_direct_ambient_worker_sends_only_bounded_safe_facts():
    client = FakeClient(_verdict())
    worker = DirectAmbientWorker(client)

    result = asyncio.run(worker.observe_with_result(_frame(), _context()))

    facts = client.facts
    assert len(facts["events"]) == 12
    assert len(facts["topics"]) <= 4
    assert len(facts["audiences"]) <= 8
    assert facts["relationship_memories"] == [
        {
            "event_id": "relationship:boundary-1",
            "subject_id": "u1",
            "kind": "boundary_pressure",
            "summary": "成员上次明确辱骂爱弥斯",
            "occurred_at": 90,
        }
    ]
    assert facts["events"][0]["id"] == "qq:2"
    assert facts["events"][-1]["parts"] == ["image", "at"]
    assert facts["persona"].keys() == {"presence", "participation"}
    rendered = json.dumps(facts, ensure_ascii=False)
    for excluded in (
        "https://secret/media.jpg",
        "/private/runtime.db",
        "private persona background",
        "private style",
        "private-plan",
        "private-message-id",
        "scene_version",
        "config_version",
    ):
        assert excluded not in rendered
    assert result.input_bytes < 6_500


def test_valid_speak_verdict_becomes_signal_then_local_assessment():
    worker = DirectAmbientWorker(FakeClient(_verdict()))

    result = asyncio.run(worker.observe_with_result(_frame(), _context()))

    assert result.diagnostic_code is None
    assert result.backend == "direct_deepseek"
    assert result.model == "deepseek-v4-flash"
    assert [item.kind for item in result.observations] == [
        "help_request",
        "participation_assessment",
    ]
    signal, assessment = result.observations
    assert signal.proposition["subject_id"] == "u1"
    assert signal.proposition["topic_id"] == "topic-1"
    assert assessment.scene_version == 3
    assert assessment.expires_at == 138
    assert assessment.evidence_event_ids == ("qq:12", "qq:13")
    assert assessment.proposition["should_participate"] is True
    assert assessment.proposition["target_confidence"] == 0.86
    assert assessment.proposition["topic_confidence"] == 0.86


def test_ambient_verdict_emits_validated_relationship_observation():
    worker = DirectAmbientWorker(
        FakeClient(
            _verdict(
                relationship_events=[
                    {
                        "kind": "warm_exchange",
                        "subject_id": "u1",
                        "severity": "ordinary",
                        "confidence": 0.91,
                        "summary": "成员认真感谢了爱弥斯",
                        "evidence_event_ids": ["qq:12"],
                        "repair_of": None,
                        "sensitivity": "normal",
                    }
                ]
            )
        )
    )

    result = asyncio.run(worker.observe_with_result(_frame(), _context()))

    assert result.diagnostic_code is None
    assert [item.kind for item in result.observations] == [
        "help_request",
        "relationship_event",
        "participation_assessment",
    ]
    relationship = result.observations[1]
    assert relationship.proposition == {
        "kind": "warm_exchange",
        "subject_id": "u1",
        "severity": "ordinary",
        "summary": "成员认真感谢了爱弥斯",
        "repair_of": None,
        "sensitivity": "normal",
    }
    assert relationship.confidence == 0.91
    assert relationship.evidence_event_ids == ("qq:12",)


def test_confirmed_repair_must_reference_an_supplied_relationship_memory():
    known = DirectAmbientWorker(
        FakeClient(
            _verdict(
                relationship_events=[
                    {
                        "kind": "repair_confirmed",
                        "subject_id": "u1",
                        "severity": "significant",
                        "confidence": 0.93,
                        "summary": "成员认真道歉并停止冒犯",
                        "evidence_event_ids": ["qq:13"],
                        "repair_of": "relationship:boundary-1",
                        "sensitivity": "normal",
                    }
                ]
            )
        )
    )
    fabricated = DirectAmbientWorker(
        FakeClient(
            _verdict(
                relationship_events=[
                    {
                        "kind": "repair_confirmed",
                        "subject_id": "u1",
                        "severity": "significant",
                        "confidence": 0.93,
                        "summary": "成员声称已经道歉",
                        "evidence_event_ids": ["qq:13"],
                        "repair_of": "relationship:invented",
                        "sensitivity": "normal",
                    }
                ]
            )
        )
    )

    known_result = asyncio.run(known.observe_with_result(_frame(), _context()))
    fabricated_result = asyncio.run(
        fabricated.observe_with_result(_frame(), _context())
    )

    assert any(
        item.kind == "relationship_event"
        and item.proposition["repair_of"] == "relationship:boundary-1"
        for item in known_result.observations
    )
    assert not any(
        item.kind == "relationship_event"
        for item in fabricated_result.observations
    )


def test_invalid_relationship_entry_does_not_invalidate_participation():
    worker = DirectAmbientWorker(
        FakeClient(
            _verdict(
                relationship_events=[
                    {
                        "kind": "invented_score_change",
                        "subject_id": "u9",
                        "severity": "huge",
                        "confidence": 2,
                        "summary": "invalid",
                        "evidence_event_ids": ["missing"],
                        "repair_of": None,
                        "sensitivity": "normal",
                        "delta": 999,
                    }
                ]
            )
        )
    )

    result = asyncio.run(worker.observe_with_result(_frame(), _context()))

    assert result.diagnostic_code is None
    assert [item.kind for item in result.observations] == [
        "help_request",
        "participation_assessment",
    ]


def test_model_cannot_propose_locally_deterministic_relationship_event():
    worker = DirectAmbientWorker(
        FakeClient(
            _verdict(
                relationship_events=[
                    {
                        "kind": "interaction",
                        "subject_id": "u1",
                        "severity": "minor",
                        "confidence": 1.0,
                        "summary": "普通互动",
                        "evidence_event_ids": ["qq:12"],
                        "repair_of": None,
                        "sensitivity": "normal",
                    }
                ]
            )
        )
    )

    result = asyncio.run(worker.observe_with_result(_frame(), _context()))

    assert not any(
        item.kind == "relationship_event" for item in result.observations
    )


def test_valid_silence_verdict_produces_only_assessment():
    worker = DirectAmbientWorker(
        FakeClient(
            _verdict(
                decision="silence",
                signal="none",
                target_id=None,
            )
        )
    )

    result = asyncio.run(worker.observe_with_result(_frame(), _context()))

    assert [item.kind for item in result.observations] == [
        "participation_assessment"
    ]
    assert result.observations[0].proposition["should_participate"] is False


def test_silence_with_empty_evidence_binds_to_newest_frozen_event():
    worker = DirectAmbientWorker(
        FakeClient(
            _verdict(
                decision="silence",
                signal="none",
                target_id=None,
                evidence_event_ids=[],
            )
        )
    )

    result = asyncio.run(worker.observe_with_result(_frame(), _context()))

    assert result.diagnostic_code is None
    assert len(result.observations) == 1
    assert result.observations[0].evidence_event_ids == ("qq:13",)
    assert result.observations[0].proposition["should_participate"] is False


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    (
        ({"decision": "maybe"}, "direct_invalid_decision"),
        ({"signal": "unknown"}, "direct_invalid_signal"),
        (
            {"decision": "speak", "signal": "none"},
            "direct_speak_without_signal",
        ),
        ({"target_id": "outside-frame"}, "direct_unknown_target"),
        ({"evidence_event_ids": []}, "direct_empty_speak_evidence"),
        (
            {"evidence_event_ids": ["qq:outside"]},
            "direct_unknown_evidence",
        ),
        ({"confidence": True}, "direct_invalid_score"),
        ({"confidence": float("nan")}, "direct_invalid_score"),
        ({"disruption": 2.0}, "direct_invalid_score"),
        ({"novelty": -0.1}, "direct_invalid_score"),
    ),
)
def test_invalid_remote_scope_or_numbers_are_not_accepted(
    overrides, expected_code
):
    worker = DirectAmbientWorker(FakeClient(_verdict(**overrides)))

    result = asyncio.run(worker.observe_with_result(_frame(), _context()))

    assert result.observations == ()
    assert result.diagnostic_code == expected_code


def test_missing_required_field_is_not_accepted():
    verdict = _verdict()
    del verdict["reason"]
    worker = DirectAmbientWorker(FakeClient(verdict))

    result = asyncio.run(worker.observe_with_result(_frame(), _context()))

    assert result.observations == ()
    assert result.diagnostic_code == "direct_missing_field"


def test_direct_failure_metadata_reaches_cognition_diagnostic():
    error = DirectCognitionError(
        "direct_timeout",
        latency_ms=6_000,
        request_bytes=2_048,
        backend="direct_deepseek",
        model="deepseek-v4-flash",
    )
    worker = DirectAmbientWorker(FakeClient(error=error))
    service = CognitionService(
        workers={"ambient_social_assessor": worker},
        budget=CognitionBudget(1, 1),
    )

    snapshot = asyncio.run(service.evaluate(_frame(), _context()))

    diagnostic = snapshot.worker_diagnostics[-1]
    assert diagnostic.status == "TIMED_OUT"
    assert diagnostic.diagnostic_code == "direct_timeout"
    assert diagnostic.provider_latency_ms == 6_000
    assert diagnostic.input_bytes == 2_048
    assert diagnostic.backend == "direct_deepseek"
    assert diagnostic.model == "deepseek-v4-flash"


def test_direct_output_failure_is_classified_as_invalid_output():
    error = DirectCognitionError(
        "direct_response_json_invalid",
        latency_ms=120,
        request_bytes=1_024,
        backend="direct_deepseek",
        model="deepseek-v4-flash",
    )
    worker = DirectAmbientWorker(FakeClient(error=error))
    service = CognitionService(
        workers={"ambient_social_assessor": worker},
        budget=CognitionBudget(1, 1),
    )

    snapshot = asyncio.run(service.evaluate(_frame(), _context()))

    diagnostic = snapshot.worker_diagnostics[-1]
    assert diagnostic.status == "INVALID_OUTPUT"
    assert diagnostic.diagnostic_code == "direct_response_json_invalid"
