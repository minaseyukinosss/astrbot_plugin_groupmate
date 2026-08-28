from __future__ import annotations

import asyncio

from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.settings import SocialRuntimeSettings
from groupmate.social_runtime.contracts import RuntimeMode, SocialEventEnvelope
from groupmate.social_runtime.knowledge.contracts import TopicUnderstandingFrame
from groupmate.social_runtime.manager import ShadowEvaluation, SocialRuntimeManager


def _event(event_id="qq:knowledge"):
    return SocialEventEnvelope.create(
        event_id=event_id,
        event_type="platform.message",
        occurred_at=100,
        received_at=100,
        persona_id="persona:1",
        group_id="g1",
        actor_id="member:1",
        source_message_id=event_id,
        correlation_id=event_id,
        causation_id=None,
        payload={"text": "原神新版本怎么样", "direct_address": False},
    )


def _topic(frame_id="knowledge-frame:test"):
    return TopicUnderstandingFrame.create(
        frame_id=frame_id,
        game_ids=("game:genshin-impact",),
        resolved_entities=(),
        resolved_terms=(),
        discourse_referents=(),
        version_reference={
            "game_id": "game:genshin-impact",
            "relative_kind": "new",
            "disclosure_kind": "none",
            "region": None,
            "platform": None,
            "confidence": 0.9,
        },
        conversation_intent_hint="version_question",
        ambiguity_codes=("risk:version_state",),
        confidence=0.9,
        supporting_knowledge_ids=("game-semantic:genshin-impact",),
    )


class _Resolver:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    def resolve(self, event, context_events, group_id, now):
        self.calls.append((event.event_id, group_id, now, len(context_events)))
        if self.fail:
            raise RuntimeError("resolver internals must not leak")
        return _topic()


class _Worker:
    name = "ambient_social_assessor"

    def __init__(self):
        self.contexts = []

    async def observe(self, _frame, context):
        self.contexts.append(context)
        return ()


def _run(tmp_path, *, fail=False):
    async def scenario():
        resolver = _Resolver(fail=fail)
        worker = _Worker()
        manager = SocialRuntimeManager(
            database_path=tmp_path / "runtime.db",
            persona_id="persona:1",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("g1",),
            cognition_workers={worker.name: worker},
            knowledge_resolver=resolver,
            clock=lambda: 100,
        )
        await manager.start()
        await manager.ingest(_event())
        evaluations = await manager.drain(now=102)
        await manager.close()
        return resolver, worker, evaluations[0]

    return asyncio.run(scenario())


def test_topic_frame_exists_before_cognition_and_cannot_authorize_speech(tmp_path):
    resolver, worker, evaluation = _run(tmp_path)

    assert resolver.calls == [("qq:knowledge", "g1", 102, 1)]
    cognitive_topic = worker.contexts[0].world_summary["topic_understanding"]
    assert cognitive_topic["frame_id"] == "knowledge-frame:test"
    assert evaluation.topic_understanding.frame_id == "knowledge-frame:test"
    assert evaluation.candidates == ()
    assert evaluation.governor_result.selected_intention_ids == ()


def test_resolver_failure_produces_safe_empty_frame_and_cognition_continues(
    tmp_path,
):
    _resolver, worker, evaluation = _run(tmp_path, fail=True)

    topic = evaluation.topic_understanding
    assert topic is not None
    assert topic.game_ids == ()
    assert topic.ambiguity_codes == ("knowledge_local_resolution_failed",)
    assert evaluation.knowledge_diagnostics == (
        "knowledge_local_resolution_failed",
    )
    assert worker.contexts[0].world_summary["topic_understanding"][
        "game_ids"
    ] == []


def test_topic_frame_capture_round_trip_is_backward_compatible():
    evaluation = ShadowEvaluation.from_capture_evidence(
        {
            "evaluation": {
                "persona_id": "persona:1",
                "request_id": "request:1",
                "runtime_mode": "SHADOW",
                "scene_version": 1,
                "config_version": 1,
                "frame": None,
                "governor_result": {
                    "outcome": "SILENCE",
                    "selected_intention_ids": [],
                    "rejected": [],
                    "reason_codes": [],
                    "reconsider_at": None,
                    "constraints": [],
                },
                "source_event": _event().to_dict(),
                "context_events": [],
                "candidates": [],
                "accepted": True,
                "status": "accepted",
            }
        }
    )
    assert evaluation.topic_understanding is None

    with_topic = ShadowEvaluation.from_capture_evidence(
        {
            "evaluation": {
                **evaluation.to_capture_evidence()["evaluation"],
                "topic_understanding": _topic().to_prompt_facts(),
            }
        }
    )
    assert with_topic.topic_understanding.frame_id == "knowledge-frame:test"


def test_knowledge_off_does_not_start_resolver_or_observer(tmp_path):
    async def scenario():
        settings = SocialRuntimeSettings.from_mapping(
            {
                "enabled_groups": ["g1"],
                "runtime_mode": "SHADOW",
                "generation_provider": "provider:test",
                "profile_enabled": False,
                "knowledge_enabled": False,
                "knowledge_web_search_enabled": False,
            }
        )
        bridge = AstrBotSocialRuntimeBridge(object(), settings, tmp_path)
        await bridge.start()
        resolver = bridge.manager.knowledge_resolver
        observer = bridge._knowledge_service
        await bridge.close()
        return resolver, observer

    resolver, observer = asyncio.run(scenario())

    assert resolver is None
    assert observer is None
    assert not (tmp_path / ".groupmate-knowledge-salt").exists()
