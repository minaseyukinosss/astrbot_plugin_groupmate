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
    TopicSnapshot,
    TriggerKind,
)
from groupmate.persona.aemeath import AemeathOutputFirewall, AemeathPersonaProvider
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
    first = store.record_social_interaction(event, now=5)
    second = store.record_social_interaction(event, now=6)
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

    db = sqlite3.connect(str(path))
    legacy_rows = db.execute("SELECT COUNT(*) FROM favorability").fetchone()[0]
    db.close()
    assert legacy_rows == 0


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
    assert memory.relationship_state == {}


def test_workflow_does_not_infer_social_event_after_send():
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
    assert memory.social_events == []
    assert memory.get_relationship_state("g1", "u1") is None


def test_first_ambiguous_romantic_address_does_not_create_negative_event():
    memory = FakeMemoryRepository()
    workflow = CognitiveWorkflow(
        generation_model=StaticGenerationModel("别乱叫呀。"),
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
        messages=(_msg(text="爱弥斯 老婆"),),
        created_at=100,
        updated_at=100,
    )
    from groupmate.models import GroupPolicy

    outcome = asyncio.run(
        workflow.evaluate(
            topic, TriggerKind.ALIAS_DIRECT, GroupPolicy(humanize_delay_enabled=False)
        )
    )

    assert outcome.sent is True
    assert memory.social_events == []
    assert memory.relationship_state == {}


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
    assert memory.relationship_state == {}
