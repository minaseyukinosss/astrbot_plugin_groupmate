"""已验证社会事件的幂等写入与关系重放。"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from groupmate.core.addressee import AddresseeResolver
from groupmate.engine.rate_limit import SlidingWindowRateLimiter
from groupmate.engine.workflow import CognitiveWorkflow
from groupmate.memory import SQLiteMemoryStore
from groupmate.models import (
    AddresseeKind,
    ChatMessage,
    SocialEvent,
    SocialEventKind,
    SocialEventStatus,
    TopicSnapshot,
    TriggerKind,
)
from groupmate.core.response_act import ResponseAct, ResponseActPlan
from groupmate.models import InteractionScene
from groupmate.social.evidence import RelationshipEvidenceWriter
from groupmate.persona.aemeath import AemeathOutputFirewall
from groupmate.policies import BehaviorPolicy, ReplyPolicy
from groupmate.social.projector import SocialStateProjector
from tests.fakes import (
    FakeClock,
    FakeMemoryRepository,
    FakePlatform,
    NullVision,
    StaticGenerationModel,
    persona_context,
)


def _msg(**overrides):
    values = {
        "message_id": "m1",
        "group_id": "g1",
        "sender_id": "u1",
        "sender_name": "Alice",
        "text": "你好",
        "timestamp": 100,
    }
    values.update(overrides)
    return ChatMessage(**values)


def _event(kind, *, event_id="e1", occurred_at=1):
    return SocialEvent(
        event_id=event_id,
        group_id="g1",
        user_id="u1",
        kind=kind,
        source_message_id="m1",
        confidence=1.0,
        occurred_at=occurred_at,
        decision_id="d1",
    )


def _behavior():
    return BehaviorPolicy(
        reply=ReplyPolicy(humanize_delay_enabled=False),
    )


def _workflow(model, memory, *, addressee_resolver=None):
    return CognitiveWorkflow(
        generation_model=model,
        vision=NullVision(),
        platform=FakePlatform(),
        memory=memory,
        persona_context=persona_context(),
        behavior=_behavior(),
        vision_enabled=True,
        output_guard=AemeathOutputFirewall(),
        rate_limiter=SlidingWindowRateLimiter(
            hourly_limit=6,
            cooldown_seconds=0,
        ),
        clock=FakeClock(200),
        addressee_resolver=addressee_resolver,
    )


def test_neutral_interaction_increases_familiarity_not_affinity():
    projector = SocialStateProjector()

    state = projector.apply_event(None, _event(SocialEventKind.NEUTRAL), now=1)

    assert state.familiarity == 1
    assert state.affinity == 0


def test_verified_friendly_tease_can_increase_affinity_once():
    projector = SocialStateProjector()

    state = projector.apply_event(
        None, _event(SocialEventKind.FRIENDLY_TEASE), now=1
    )

    assert state.affinity == 1
    assert state.boundary_pressure == 0


def test_apology_repairs_slowly_without_clearing_boundary_pressure():
    projector = SocialStateProjector()
    harmed = projector.apply_event(
        None, _event(SocialEventKind.HARASSMENT, event_id="e1"), now=1
    )

    repaired = projector.apply_event(
        harmed,
        _event(SocialEventKind.APOLOGY, event_id="e2", occurred_at=2),
        now=2,
    )

    assert repaired.affinity > harmed.affinity
    assert repaired.affinity < 0
    assert 0 < repaired.boundary_pressure < harmed.boundary_pressure


def test_only_explicit_verified_boundary_event_reduces_affinity():
    projector = SocialStateProjector()
    neutral = projector.apply_event(
        None, _event(SocialEventKind.NEUTRAL, event_id="neutral"), now=1
    )

    crossed = projector.apply_event(
        neutral,
        _event(SocialEventKind.BOUNDARY_PUSH, event_id="boundary", occurred_at=2),
        now=2,
    )

    assert neutral.affinity == 0
    assert crossed.affinity < neutral.affinity
    assert crossed.boundary_pressure > neutral.boundary_pressure


def test_social_event_idempotent_and_replay(tmp_path):
    path = tmp_path / "social.db"
    store = SQLiteMemoryStore(path)
    event = _event(SocialEventKind.THANKS, occurred_at=5)
    first = store.record_social_interaction("aemeath", event, now=5)
    second = store.record_social_interaction("aemeath", event, now=6)
    assert first is not None
    assert second is not None
    assert first.affinity == second.affinity
    assert len(store.list_social_events("aemeath", "g1", user_id="u1")) == 1

    wiped = store.get_relationship_state("aemeath", "g1", "u1")
    assert wiped is not None
    rebuilt = store.rebuild_relationship_state(
        "aemeath", "g1", "u1", seed_affinity=0, now=10
    )
    assert rebuilt.affinity == wiped.affinity
    assert rebuilt.interaction_count == wiped.interaction_count
    store.close()

    db = sqlite3.connect(str(path))
    tables = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    db.close()
    assert "favorability" not in tables


def test_legacy_runtime_interfaces_and_keyword_classifier_are_removed():
    store_methods = set(dir(SQLiteMemoryStore))

    assert "get_favorability" not in store_methods
    assert "set_favorability" not in store_methods
    assert "adjust_favorability" not in store_methods
    root = Path(__file__).resolve().parents[1]
    assert not (root / "groupmate" / "core" / "favorability.py").exists()
    assert not (root / "groupmate" / "social" / "events.py").exists()


def test_ambiguous_and_silence_do_not_write_personal_state():
    memory = FakeMemoryRepository()
    workflow = _workflow(StaticGenerationModel("<SILENCE>"), memory)
    topic = TopicSnapshot(
        topic_id="t1",
        group_id="g1",
        messages=(
            _msg(
                text="@Bob @Carol 你们看",
                mentioned_user_ids=("u2", "u3"),
            ),
        ),
        created_at=100,
        updated_at=100,
    )
    outcome = asyncio.run(
        workflow.evaluate(topic, TriggerKind.ALIAS_DIRECT, _behavior())
    )
    assert outcome.sent is False
    assert memory.social_events == []
    assert memory.relationship_state == {}


def test_workflow_does_not_infer_social_event_after_send():
    memory = FakeMemoryRepository()
    workflow = _workflow(StaticGenerationModel("在呢。"), memory)
    topic = TopicSnapshot(
        topic_id="t1",
        group_id="g1",
        messages=(_msg(text="爱弥斯 在吗"),),
        created_at=100,
        updated_at=100,
    )
    outcome = asyncio.run(
        workflow.evaluate(topic, TriggerKind.ALIAS_DIRECT, _behavior())
    )
    assert outcome.sent is True
    assert memory.social_events == []
    assert memory.get_relationship_state("aemeath", "g1", "u1") is None


def test_first_ambiguous_romantic_address_does_not_create_negative_event():
    memory = FakeMemoryRepository()
    workflow = _workflow(StaticGenerationModel("别乱叫呀。"), memory)
    topic = TopicSnapshot(
        topic_id="t1",
        group_id="g1",
        messages=(_msg(text="爱弥斯 老婆"),),
        created_at=100,
        updated_at=100,
    )
    outcome = asyncio.run(
        workflow.evaluate(
            topic, TriggerKind.ALIAS_DIRECT, _behavior()
        )
    )

    assert outcome.sent is True
    assert memory.social_events == []
    assert memory.relationship_state == {}


def test_multi_mention_silence_skips_personal_social_write():
    memory = FakeMemoryRepository()
    workflow = _workflow(
        StaticGenerationModel("嗯。"),
        memory,
        addressee_resolver=AddresseeResolver(),
    )
    topic = TopicSnapshot(
        topic_id="t1",
        group_id="g1",
        messages=(
            _msg(
                text="@Bob @Carol 你们看",
                mentioned_user_ids=("u2", "u3"),
            ),
        ),
        created_at=100,
        updated_at=100,
    )
    targeting = AddresseeResolver().resolve(
        topic, TriggerKind.ALIAS_DIRECT, bot_id="bot"
    )
    assert targeting.social_target.kind is AddresseeKind.AMBIGUOUS
    outcome = asyncio.run(
        workflow.evaluate(
            topic, TriggerKind.ALIAS_DIRECT, _behavior()
        )
    )
    assert outcome.sent is False
    assert outcome.reason == "inhibit:ambiguous_target"
    assert memory.social_events == []
    assert memory.relationship_state == {}


class EvidenceModel:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def extract_relationship_evidence(self, **kwargs):
        del kwargs
        self.calls += 1
        return self.payload


class EvidenceGenerationModel(StaticGenerationModel):
    def __init__(self, text, payload):
        super().__init__(text)
        self.payload = payload
        self.evidence_calls = 0

    async def extract_relationship_evidence(self, **kwargs):
        del kwargs
        self.evidence_calls += 1
        return self.payload


def _targeting(topic, trigger=TriggerKind.ALIAS_DIRECT):
    return AddresseeResolver().resolve(
        topic,
        trigger,
        aliases=("爱弥斯",),
        bot_id="bot",
    )


def test_context_evidence_writer_accepts_grounded_single_owner_thanks():
    memory = FakeMemoryRepository()
    model = EvidenceModel(
        {
            "kind": "THANKS",
            "confidence": 0.94,
            "evidence_quote": "谢谢你刚才帮我",
            "reason_code": "direct_thanks",
        }
    )
    topic = TopicSnapshot(
        topic_id="t1",
        group_id="g1",
        messages=(_msg(text="爱弥斯，谢谢你刚才帮我"),),
        created_at=100,
        updated_at=100,
    )
    event = asyncio.run(
        RelationshipEvidenceWriter(
            memory, model, persona_id="aemeath"
        ).process(
            topic,
            _targeting(topic),
            trigger=TriggerKind.ALIAS_DIRECT,
            decision_id="d1",
            now=110,
            response_act=ResponseActPlan(
                ResponseAct.RECIPROCATE,
                InteractionScene.SOCIAL_RESPONSE,
                ("social_reciprocity",),
            ),
            reply_text="不用客气。",
        )
    )

    assert event is not None
    assert event.kind is SocialEventKind.THANKS
    assert event.status is SocialEventStatus.PENDING
    assert event.user_id == "u1"
    assert memory.get_relationship_state("aemeath", "g1", "u1") is None


def test_context_evidence_writer_auto_applies_only_after_group_quality_gate():
    memory = FakeMemoryRepository()
    memory.relationship_learning_quality = lambda persona_id, group_id=None: {
        "reviewed_count": 20,
        "error_rate": 0.1,
    }
    model = EvidenceModel(
        {
            "kind": "THANKS",
            "confidence": 0.94,
            "evidence_quote": "谢谢你刚才帮我",
            "reason_code": "direct_thanks",
        }
    )
    topic = TopicSnapshot(
        topic_id="t1",
        group_id="g1",
        messages=(_msg(text="爱弥斯，谢谢你刚才帮我"),),
        created_at=100,
        updated_at=100,
    )

    event = asyncio.run(
        RelationshipEvidenceWriter(
            memory,
            model,
            persona_id="aemeath",
            active_groups=("g1",),
        ).process(
            topic,
            _targeting(topic),
            trigger=TriggerKind.ALIAS_DIRECT,
            decision_id="d1",
            now=110,
            response_act=ResponseActPlan(
                ResponseAct.RECIPROCATE,
                InteractionScene.SOCIAL_RESPONSE,
                ("social_reciprocity",),
            ),
            reply_text="不用客气。",
        )
    )

    assert event.status is SocialEventStatus.ACCEPTED
    assert memory.get_relationship_state("aemeath", "g1", "u1").affinity == 2


def test_context_evidence_writer_rejects_quote_not_in_source():
    memory = FakeMemoryRepository()
    model = EvidenceModel(
        {
            "kind": "PRAISE",
            "confidence": 0.99,
            "evidence_quote": "你是全世界最厉害的",
            "reason_code": "invented_quote",
        }
    )
    topic = TopicSnapshot(
        topic_id="t1",
        group_id="g1",
        messages=(_msg(text="爱弥斯，在吗"),),
        created_at=100,
        updated_at=100,
    )
    event = asyncio.run(
        RelationshipEvidenceWriter(
            memory, model, persona_id="aemeath"
        ).process(
            topic,
            _targeting(topic),
            trigger=TriggerKind.ALIAS_DIRECT,
            decision_id="d1",
            now=110,
            response_act=ResponseActPlan(
                ResponseAct.ANSWER,
                InteractionScene.DIRECT_ADDRESS,
                ("content_response",),
            ),
            reply_text="在。",
        )
    )

    assert event is None
    assert memory.social_events == []


def test_negative_evidence_requires_real_boundary_context():
    memory = FakeMemoryRepository()
    model = EvidenceModel(
        {
            "kind": "BOUNDARY_PUSH",
            "confidence": 0.99,
            "evidence_quote": "老婆",
            "reason_code": "single_address",
        }
    )
    topic = TopicSnapshot(
        topic_id="t1",
        group_id="g1",
        messages=(_msg(text="爱弥斯 老婆"),),
        created_at=100,
        updated_at=100,
    )
    writer = RelationshipEvidenceWriter(memory, model, persona_id="aemeath")
    event = asyncio.run(
        writer.process(
            topic,
            _targeting(topic),
            trigger=TriggerKind.ALIAS_DIRECT,
            decision_id="d1",
            now=110,
            response_act=ResponseActPlan(
                ResponseAct.PLAYFUL_REPLY,
                InteractionScene.DIRECT_ADDRESS,
                ("playful_signal",),
            ),
            reply_text="少来。",
        )
    )

    assert event is None
    assert memory.relationship_state == {}


def test_workflow_records_verified_relationship_evidence_after_send():
    memory = FakeMemoryRepository()
    model = EvidenceGenerationModel(
        "不用客气。",
        {
            "kind": "THANKS",
            "confidence": 0.95,
            "evidence_quote": "谢谢你",
            "reason_code": "direct_thanks",
        },
    )
    workflow = _workflow(model, memory)
    topic = TopicSnapshot(
        topic_id="t1",
        group_id="g1",
        messages=(_msg(text="爱弥斯，谢谢你"),),
        created_at=100,
        updated_at=100,
    )

    outcome = asyncio.run(
        workflow.evaluate(topic, TriggerKind.ALIAS_DIRECT, _behavior())
    )

    assert outcome.sent is True
    assert model.evidence_calls == 1
    assert len(memory.social_events) == 1
    assert memory.social_events[0].kind is SocialEventKind.THANKS
