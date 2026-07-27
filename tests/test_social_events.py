"""社会事件分类、幂等写入与关系重放。"""

from __future__ import annotations

import asyncio

from groupmate.core.addressee import AddresseeResolver
from groupmate.engine.rate_limit import SlidingWindowRateLimiter
from groupmate.engine.workflow import CognitiveWorkflow
from groupmate.memory import SQLiteMemoryStore
from groupmate.models import (
    AddresseeKind,
    ChatMessage,
    SocialEventKind,
    TopicSnapshot,
    TriggerKind,
)
from groupmate.persona.aemeath import AemeathOutputFirewall, AemeathPersonaProvider
from groupmate.social.events import SocialEventClassifier
from groupmate.social.projector import SocialStateProjector
from tests.fakes import (
    FakeClock,
    FakeMemoryRepository,
    FakePlatform,
    NullVision,
    StaticGenerationModel,
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


def test_classifier_detects_thanks_and_harassment():
    classifier = SocialEventClassifier()
    thanks = classifier.classify(_msg(text="谢谢你呀"), user_id="u1")
    assert thanks.kind is SocialEventKind.THANKS
    bad = classifier.classify(_msg(text="你给我滚"), user_id="u1")
    assert bad.kind is SocialEventKind.HARASSMENT


def test_friendly_tease_does_not_crash_affinity():
    projector = SocialStateProjector()
    event = SocialEventClassifier().classify(
        _msg(text="哈哈你傻"), user_id="u1", occurred_at=1
    )
    assert event.kind is SocialEventKind.FRIENDLY_TEASE
    state = projector.apply_event(None, event, now=1)
    assert state.affinity == 1
    assert state.boundary_pressure == 0


def test_social_event_idempotent_and_replay(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "social.db")
    event = SocialEventClassifier().classify(
        _msg(text="谢谢"), user_id="u1", occurred_at=5, decision_id="d1"
    )
    first = store.record_social_interaction(event, soft_trigger=False, now=5)
    second = store.record_social_interaction(event, soft_trigger=False, now=6)
    assert first is not None
    assert second is not None
    assert first.affinity == second.affinity
    assert len(store.list_social_events("g1", user_id="u1")) == 1

    wiped = store.get_relationship_state("g1", "u1")
    assert wiped is not None
    rebuilt = store.rebuild_relationship_state("g1", "u1", seed_affinity=0, now=10)
    assert rebuilt.affinity == wiped.affinity
    assert rebuilt.interaction_count == wiped.interaction_count
    store.close()


def test_ambiguous_and_silence_do_not_write_personal_state():
    memory = FakeMemoryRepository()
    workflow = CognitiveWorkflow(
        generation_model=StaticGenerationModel("<SILENCE>"),
        vision=NullVision(),
        platform=FakePlatform(),
        memory=memory,
        persona=AemeathPersonaProvider(),
        output_guard=AemeathOutputFirewall(max_chars=60),
        rate_limiter=SlidingWindowRateLimiter(hourly_limit=6, cooldown_seconds=0),
        clock=FakeClock(200),
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
    from groupmate.models import GroupPolicy

    policy = GroupPolicy(humanize_delay_enabled=False)
    outcome = asyncio.run(
        workflow.evaluate(topic, TriggerKind.ALIAS_DIRECT, policy)
    )
    assert outcome.sent is False
    assert memory.social_events == []
    assert memory.favorability == {}


def test_workflow_records_social_only_after_send():
    memory = FakeMemoryRepository()
    workflow = CognitiveWorkflow(
        generation_model=StaticGenerationModel("在呢。"),
        vision=NullVision(),
        platform=FakePlatform(),
        memory=memory,
        persona=AemeathPersonaProvider(),
        output_guard=AemeathOutputFirewall(max_chars=60),
        rate_limiter=SlidingWindowRateLimiter(hourly_limit=6, cooldown_seconds=0),
        clock=FakeClock(200),
    )
    topic = TopicSnapshot(
        topic_id="t1",
        group_id="g1",
        messages=(_msg(text="爱弥斯 在吗"),),
        created_at=100,
        updated_at=100,
    )
    from groupmate.models import GroupPolicy

    policy = GroupPolicy(humanize_delay_enabled=False)
    outcome = asyncio.run(
        workflow.evaluate(topic, TriggerKind.ALIAS_DIRECT, policy)
    )
    assert outcome.sent is True
    assert len(memory.social_events) == 1
    assert memory.get_favorability("g1", "u1") == 2


def test_multi_mention_send_still_skips_personal_social_write():
    memory = FakeMemoryRepository()
    workflow = CognitiveWorkflow(
        generation_model=StaticGenerationModel("嗯。"),
        vision=NullVision(),
        platform=FakePlatform(),
        memory=memory,
        persona=AemeathPersonaProvider(),
        output_guard=AemeathOutputFirewall(max_chars=60),
        rate_limiter=SlidingWindowRateLimiter(hourly_limit=6, cooldown_seconds=0),
        clock=FakeClock(200),
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
    from groupmate.models import GroupPolicy

    outcome = asyncio.run(
        workflow.evaluate(
            topic, TriggerKind.ALIAS_DIRECT, GroupPolicy(humanize_delay_enabled=False)
        )
    )
    assert outcome.sent is True
    assert memory.social_events == []
    assert memory.favorability == {}


def test_social_flag_off_uses_legacy_favorability_delta():
    memory = FakeMemoryRepository()
    workflow = CognitiveWorkflow(
        generation_model=StaticGenerationModel("在呢。"),
        vision=NullVision(),
        platform=FakePlatform(),
        memory=memory,
        persona=AemeathPersonaProvider(),
        output_guard=AemeathOutputFirewall(max_chars=60),
        rate_limiter=SlidingWindowRateLimiter(hourly_limit=6, cooldown_seconds=0),
        clock=FakeClock(200),
    )
    topic = TopicSnapshot(
        topic_id="t1",
        group_id="g1",
        messages=(_msg(text="爱弥斯 在吗"),),
        created_at=100,
        updated_at=100,
    )
    from groupmate.models import GroupPolicy

    outcome = asyncio.run(
        workflow.evaluate(
            topic,
            TriggerKind.ALIAS_DIRECT,
            GroupPolicy(humanize_delay_enabled=False, v3_social_enabled=False),
        )
    )
    assert outcome.sent is True
    assert memory.social_events == []
    assert memory.get_favorability("g1", "u1") == 2
