from __future__ import annotations

import asyncio
import json
from dataclasses import replace

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
        self.calls = 0

    def input_bytes(self, facts):
        return DeepSeekCognitionClient(
            api_key="test",
            api_base="https://api.deepseek.com",
            model="deepseek-v4-flash",
            transport=self,
        ).input_bytes(facts)

    async def classify(self, facts):
        self.calls += 1
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
                "source_message_id": f"message:{index}",
                "actor_id": "u1" if index % 2 else "u2",
                "scene_version": 999,
                "payload": {
                    "text": "这是一段群聊文本" * 80,
                    "sender": {"id": "u1", "name": "夏夏"},
                    "reply_to": None,
                    "reply_to_actor_id": None,
                    "mentions": [],
                    "mentions_bot": False,
                    "reply_to_bot": False,
                    "bot_id": "bot:1",
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
                    "message_ids": (
                        ["message:12", "message:13"]
                        if index == 1
                        else [f"private-message-id:{index}"] * 20
                    ),
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
            "bot_actor_id": "bot:1",
            "conversation_lease": {
                "target_id": "u1",
                "topic_id": "topic-1",
                "source_plan_id": "private-plan",
                "opened_at": 70,
                "expires_at": 120,
                "remaining_turns": 1,
            },
            "persona_profile": {
                "identity": {
                    "name": "爱弥斯",
                    "aliases": ["小爱"],
                    "background": "private persona background",
                },
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
            "member_context": {
                "members": [
                    {
                        "subject_id": "u1",
                        "aliases": ["夏夏", "小夏"],
                        "addressing_habits": ["被叫小夏时通常是在直接呼唤"],
                        "private_portrait": "这是不该进入参与判断的完整个人画像",
                    }
                ],
                "relations": [
                    {
                        "source_member_id": "u1",
                        "target_member_id": "u2",
                        "relation_type": "technical_peer",
                    }
                ],
            },
            "database_path": "/private/runtime.db",
        },
        constraints=("no_side_effects", "evidence_required"),
        token_budget=1024,
    )


def _verdict(**overrides):
    value = {
        "decision": "speak",
        "opportunity_kind": "open_question",
        "anchor_event_id": "qq:13",
        "evidence_event_ids": ["qq:12", "qq:13"],
        "confidence": 0.86,
        "disruption": 0.1,
        "novelty": 0.82,
        "reason": "成员提出了可以直接帮助的问题",
    }
    value.update(overrides)
    return value


def test_history_can_explain_participation_but_cannot_become_reply_anchor():
    history = {"event_id": "bot:previous", "actor_id": "bot:1", "occurred_at": 90,
               "payload": {"text": "你最近玩什么？", "is_self": True, "origin_kind": "BOT_TEXT"}}
    context = replace(_context(), context_events=(history,))
    client = FakeClient(_verdict(context_evidence_event_ids=["bot:previous"]))
    board = asyncio.run(CognitionService(budget=CognitionBudget(1, 1), workers={
        "ambient_social_assessor": DirectAmbientWorker(client)
    }).evaluate(_frame(), context))
    assessment = next(entry.observation for entry in board.entries
                      if entry.observation.kind == "participation_assessment")
    assert assessment.proposition["context_evidence_event_ids"] == ("bot:previous",)
    from groupmate.social_runtime.scene_actor import safe_cognitive_observation
    saved = safe_cognitive_observation(assessment)["proposition"]
    assert saved["context_evidence_event_ids"] == ["bot:previous"]
    assert saved["anchor_event_id"] == "qq:13"
    assert "bot:previous" not in assessment.evidence_event_ids
    assert client.facts["context_events"][0]["text"] == "你最近玩什么？"
    client.verdict = _verdict(anchor_event_id="bot:previous")
    result = asyncio.run(DirectAmbientWorker(client).observe_with_result(_frame(), context))
    assert result.diagnostic_code == "direct_unknown_anchor"
    client.verdict = _verdict(context_evidence_event_ids=["not-provided"])
    result = asyncio.run(DirectAmbientWorker(client).observe_with_result(_frame(), context))
    assert result.diagnostic_code == "direct_unknown_evidence"


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
    assert facts["member_context"] == {
        "members": [
            {
                "subject_id": "u1",
                "aliases": ["夏夏", "小夏"],
                "addressing_habits": ["被叫小夏时通常是在直接呼唤"],
            }
        ],
        "relations": [
            {
                "source_member_id": "u1",
                "target_member_id": "u2",
                "relation_type": "technical_peer",
            }
        ],
    }
    assert facts["events"][0]["id"] == "qq:2"
    assert facts["events"][-1]["parts"] == ["image", "at"]
    assert facts["bot_names"] == ["爱弥斯", "小爱"]
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
        "这是不该进入参与判断的完整个人画像",
    ):
        assert excluded not in rendered
    # Includes the relation output protocol; the shared dialogue text budget is unchanged.
    assert result.input_bytes < 10_500


def test_direct_ambient_worker_forwards_confirmed_boundaries():
    context = _context()
    world = dict(context.world_summary)
    member_context = dict(world["member_context"])
    members = [
        {
            **member_context["members"][0],
            "boundaries": ["不拿考试成绩开玩笑"],
        }
    ]
    context = replace(
        context,
        world_summary={
            **world,
            "member_context": {**member_context, "members": members},
        },
    )
    client = FakeClient(_verdict())

    asyncio.run(DirectAmbientWorker(client).observe_with_result(_frame(), context))

    assert client.facts["member_context"]["members"][0]["boundaries"] == [
        "不拿考试成绩开玩笑"
    ]


def _owned_reply_context():
    context = _context()
    current = {**context.focus_events[-1], "event_type": "platform.message",
               "persona_id": "p", "group_id": context.group_id, "occurred_at": 100,
               "payload": {"text": "是我理解错了", "bot_id": "bot:1"}}
    history = {"event_id": "bot:previous", "event_type": "delivery.sent",
               "actor_id": "bot:1", "persona_id": "p", "group_id": context.group_id,
               "occurred_at": 90, "payload": {"text": "刚才是在说哪个意思？",
               "is_self": True, "target_id": "u1", "delivery_confirmed": True,
               "current_dialogue_session": True}}
    return replace(context, focus_events=(current,), context_events=(history, current))


def test_owned_reply_relation_can_resolve_silence_without_raising_scores():
    from groupmate.social_runtime.intentions import IntentionEngine
    from groupmate.social_runtime.scene_actor import safe_cognitive_observation

    relation = dict(kind="answers_bot", anchor_event_id="qq:13",
                    bot_event_id="bot:previous", confidence=0.9)
    client = FakeClient({"dialogue_relation": relation, "disruption": 0.1, "novelty": 0.82})
    board = asyncio.run(CognitionService(budget=CognitionBudget(1, 1), workers={
        "ambient_social_assessor": DirectAmbientWorker(client)
    }).evaluate(_frame(), _owned_reply_context()))
    assessment = next(e.observation for e in board.entries if e.observation.kind == "participation_assessment")
    assert assessment.proposition["decision"] == "speak"
    assert assessment.proposition["opportunity_kind"] == "bot_context"
    assert assessment.evidence_event_ids == ("qq:13",)
    assert assessment.confidence == 0.9
    saved = safe_cognitive_observation(assessment)["proposition"]
    assert saved["dialogue_relation_applied"] is True
    assert saved["dialogue_bot_event_id"] == "bot:previous"
    assert saved["context_evidence_event_ids"] == ["bot:previous"]
    candidates = IntentionEngine().propose(board, now=108)
    assert candidates[0].kind == "RESPOND_CONTEXT"
    assert candidates[0].disruption_cost == 0.1
    assert "lease" not in client.facts
    assert client.facts["dialogue_candidates"] == [dict(
        anchor_event_id="qq:13", bot_event_id="bot:previous", target_id="u1",
        bot_is_confirmed_self=True, same_current_session=True)]
    mixed = FakeClient(_verdict(decision="silence", opportunity_kind="none",
        anchor_event_id=None, evidence_event_ids=[], dialogue_relation=relation))
    mixed_board = asyncio.run(CognitionService(budget=CognitionBudget(1, 1), workers={
        "ambient_social_assessor": DirectAmbientWorker(mixed)
    }).evaluate(_frame(), _owned_reply_context()))
    mixed_assessment = next(e.observation for e in mixed_board.entries
                            if e.observation.kind == "participation_assessment")
    assert mixed_assessment.proposition["decision"] == "speak"
    assert mixed_assessment.confidence == 0.86
    assert mixed_assessment.proposition["original_decision"] == "silence"
    displaced = replace(_owned_reply_context(), world_summary={
        **_owned_reply_context().world_summary,
        "conversation_lease": {**_owned_reply_context().world_summary["conversation_lease"],
                               "target_id": "u2"},
    })
    displaced_facts = DirectAmbientWorker._facts(_frame(), displaced)
    assert "lease" not in displaced_facts
    assert displaced_facts["dialogue_candidates"] == client.facts["dialogue_candidates"]


def test_owned_reply_relation_rejects_unproven_history_and_preserves_non_reply_verdicts():
    context = _owned_reply_context()
    history, current = context.context_events
    relation = dict(kind="answers_bot", anchor_event_id="qq:13",
                    bot_event_id="bot:previous", confidence=0.9)
    verdict = _verdict(decision="silence", opportunity_kind="none",
                       anchor_event_id=None, evidence_event_ids=[], dialogue_relation=relation)
    bad_histories = [
        {**history, "event_id": "unknown"}, {**history, "group_id": "other"},
        {**history, "persona_id": "other"}, {**history, "occurred_at": 101},
        *({**history, "payload": {**history["payload"], key: value}} for key, value in (
            ("target_id", "u2"), ("delivery_confirmed", False), ("current_dialogue_session", False))),
    ]
    for bad in bad_histories:
        result = asyncio.run(DirectAmbientWorker(FakeClient(verdict)).observe_with_result(
            _frame(), replace(context, context_events=(bad, current))))
        assert result.diagnostic_code == "direct_invalid_dialogue_relation"
        assert not result.observations
    for invalid in ([], {**relation, "kind": "invented"}, {**relation, "confidence": True}, None):
        value = {**verdict, "dialogue_relation": invalid}
        if invalid is None:
            del value["dialogue_relation"]
        result = asyncio.run(DirectAmbientWorker(FakeClient(value))
                             .observe_with_result(_frame(), context))
        assert result.diagnostic_code == "direct_invalid_dialogue_relation"
    for non_reply in ({**relation, "confidence": 0.7},
                      {**relation, "kind": "closes_dialogue"},
                      {**relation, "kind": "other_exchange", "bot_event_id": None},
                      {**relation, "kind": "none", "anchor_event_id": None, "bot_event_id": None}):
        result = asyncio.run(DirectAmbientWorker(FakeClient({
            **verdict, "dialogue_relation": non_reply
        })).observe_with_result(_frame(), context))
        assert result.diagnostic_code is None
        assert result.observations[-1].proposition["decision"] == "silence"


def test_ambient_event_budget_preserves_current_detail_and_reply_parent():
    context = _context()
    current = context.focus_events[-1]
    parent = context.focus_events[-2]
    current["payload"]["text"] = (
        "前面先说明了一些背景，真正关键的是：只在 Python 3.13 下出现取消异常。"
    )
    current["payload"]["reply_to"] = parent["event_id"]
    parent["payload"]["text"] = "父消息里的方案是把任务放进 TaskGroup。"
    client = FakeClient(_verdict())

    asyncio.run(DirectAmbientWorker(client).observe_with_result(_frame(), context))

    events = {item["id"]: item for item in client.facts["events"]}
    assert "Python 3.13 下出现取消异常" in events["qq:13"]["text"]
    assert "TaskGroup" in events["qq:12"]["text"]
    assert sum(len(item["text"]) for item in events.values()) <= 2400


def test_valid_speak_verdict_becomes_signal_then_local_assessment():
    worker = DirectAmbientWorker(FakeClient(_verdict()))

    result = asyncio.run(worker.observe_with_result(_frame(), _context()))

    assert result.diagnostic_code is None
    assert result.backend == "direct_deepseek"
    assert result.model == "deepseek-v4-flash"
    assert [item.kind for item in result.observations] == [
        "open_question",
        "participation_assessment",
    ]
    signal, assessment = result.observations
    assert signal.proposition["subject_id"] == "u1"
    assert signal.proposition["topic_id"] == "topic-1"
    assert signal.proposition["anchor_event_id"] == "qq:13"
    assert assessment.scene_version == 3
    assert assessment.expires_at == 138
    assert assessment.evidence_event_ids == ("qq:12", "qq:13")
    assert assessment.proposition["should_participate"] is True
    assert assessment.proposition["target_confidence"] == 0.86
    assert assessment.proposition["topic_confidence"] == 0.86
    assert assessment.proposition["opportunity_kind"] == "open_question"
    assert assessment.proposition["anchor_event_id"] == "qq:13"


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
        "open_question",
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
        "open_question",
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
                opportunity_kind="none",
                anchor_event_id=None,
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
                opportunity_kind="none",
                anchor_event_id=None,
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
        ({"opportunity_kind": "unknown"}, "direct_invalid_opportunity"),
        (
            {"decision": "speak", "opportunity_kind": "none"},
            "direct_speak_without_opportunity",
        ),
        ({"anchor_event_id": "qq:outside"}, "direct_unknown_anchor"),
        ({"evidence_event_ids": []}, "direct_empty_speak_evidence"),
        (
            {"evidence_event_ids": ["qq:12"]},
            "direct_anchor_missing_from_evidence",
        ),
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


def test_explicit_reply_to_another_member_is_a_local_silence_without_model_call():
    context = _context()
    context.focus_events[-1]["payload"]["reply_to"] = "message:12"
    context.focus_events[-1]["payload"]["reply_to_actor_id"] = "u2"
    client = FakeClient(_verdict())

    result = asyncio.run(
        DirectAmbientWorker(client).observe_with_result(_frame(), context)
    )

    assert client.calls == 0
    assert [item.kind for item in result.observations] == [
        "participation_assessment"
    ]
    assessment = result.observations[0]
    assert assessment.proposition["decision"] == "silence"
    assert assessment.proposition["hard_block_reason"] == "addressed_elsewhere"
    assert assessment.proposition["anchor_event_id"] == "qq:13"


def test_explicit_mention_of_another_member_is_also_locally_blocked():
    context = _context()
    context.focus_events[-1]["payload"]["mentions"] = ["u2"]
    client = FakeClient(_verdict())

    result = asyncio.run(
        DirectAmbientWorker(client).observe_with_result(_frame(), context)
    )

    assert client.calls == 0
    assert result.observations[0].proposition["hard_block_reason"] == (
        "addressed_elsewhere"
    )


def test_resolved_bot_reply_is_not_mistaken_for_another_member():
    context = _context()
    context.focus_events[-1]["payload"]["reply_to_actor_id"] = "bot:1"
    client = FakeClient(_verdict(opportunity_kind="bot_context"))

    result = asyncio.run(
        DirectAmbientWorker(client).observe_with_result(_frame(), context)
    )

    assert client.calls == 1
    assert result.diagnostic_code is None
    assert client.facts["direction"]["address_scope"] == "BOT"


def test_model_anchor_freezes_target_and_topic_from_local_event_facts():
    worker = DirectAmbientWorker(
        FakeClient(
            _verdict(
                opportunity_kind="topic_opening",
                anchor_event_id="qq:12",
                evidence_event_ids=["qq:12"],
            )
        )
    )

    result = asyncio.run(worker.observe_with_result(_frame(), _context()))

    opportunity, assessment = result.observations
    assert opportunity.kind == "topic_opening"
    assert opportunity.proposition["subject_id"] == "u2"
    assert opportunity.proposition["topic_id"] == "topic-1"
    assert assessment.proposition["subject_id"] == "u2"
    assert assessment.proposition["topic_id"] == "topic-1"


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
