"""MemoryWriter 抽取、写入与主回复隔离。"""

from __future__ import annotations

import asyncio

from groupmate.core.addressee import AddresseeResolver
from groupmate.engine.rate_limit import SlidingWindowRateLimiter
from groupmate.engine.workflow import CognitiveWorkflow
from groupmate.memory import SQLiteMemoryStore
from groupmate.memory.memory_writer import MemoryWriter
from groupmate.memory.privacy import claim_hash
from groupmate.models import (
    AddresseeKind,
    AddresseeResolution,
    CandidateStatus,
    ChatMessage,
    GroupPolicy,
    MemoryItem,
    MemoryKind,
    MemoryScope,
    MemoryStatus,
    TargetingDecision,
    TopicSnapshot,
    TriggerKind,
)
from groupmate.persona.aemeath import AemeathOutputFirewall, AemeathPersonaProvider
from tests.fakes import (
    FakeClock,
    FakeMemoryRepository,
    FakePlatform,
    NullVision,
    StaticGenerationModel,
)


def _policy(**overrides) -> GroupPolicy:
    values = {
        "humanize_delay_enabled": False,
        "v3_memory_writer_enabled": True,
    }
    values.update(overrides)
    return GroupPolicy(**values)


def _user_targeting(user_id: str = "u1") -> TargetingDecision:
    subject = AddresseeResolution(
        kind=AddresseeKind.USER,
        target_user_ids=(user_id,),
        confidence=0.9,
        reason_codes=("direct_sender",),
    )
    return TargetingDecision(
        reply_audience=subject,
        memory_subject=subject,
        social_target=subject,
    )


def _ambiguous_targeting(user_id: str = "u2") -> TargetingDecision:
    subject = AddresseeResolution(
        kind=AddresseeKind.AMBIGUOUS,
        target_user_ids=(user_id,),
        confidence=0.4,
        reason_codes=("recount_unconfirmed", "no_personal_memory"),
    )
    audience = AddresseeResolution(
        kind=AddresseeKind.USER,
        target_user_ids=("u1",),
        confidence=0.8,
    )
    return TargetingDecision(
        reply_audience=audience,
        memory_subject=subject,
        social_target=subject,
    )


def _topic(text: str, *, group_id="g1", sender="u1") -> TopicSnapshot:
    message = ChatMessage(
        message_id="m1",
        group_id=group_id,
        sender_id=sender,
        sender_name="Alice",
        text=text,
        timestamp=100,
    )
    return TopicSnapshot(
        topic_id="t-mem",
        group_id=group_id,
        messages=(message,),
        created_at=100,
        updated_at=100,
    )


def test_sensitive_text_writes_rejected_not_memory(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "m.db")
    writer = MemoryWriter(store)
    writer.process(
        _topic("记住我的密码是 secret123"),
        _user_targeting(),
        decision_id="d1",
        now=100,
    )
    candidates = store.list_memory_candidates("g1")
    assert candidates
    assert all(item.status is CandidateStatus.REJECTED for item in candidates)
    assert store.list_memories("g1", now=100) == []
    store.close()


def test_ambiguous_subject_has_zero_personal_accepted(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "m.db")
    writer = MemoryWriter(store)
    writer.process(
        _topic("听说他明天考试", sender="u1"),
        _ambiguous_targeting("u2"),
        decision_id="d1",
        now=100,
    )
    accepted = [
        item
        for item in store.list_memory_candidates("g1")
        if item.status is CandidateStatus.ACCEPTED
        and item.scope is MemoryScope.USER_IN_GROUP
    ]
    assert accepted == []
    assert store.list_memories("g1", now=100) == []
    store.close()


def test_joke_not_auto_accepted(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "m.db")
    writer = MemoryWriter(store)
    writer.process(
        _topic("开玩笑的别当真，我喜欢榴莲"),
        _user_targeting(),
        decision_id="d1",
        now=100,
    )
    assert store.list_memories("g1", now=100) == []
    assert any(
        item.status is CandidateStatus.REJECTED
        for item in store.list_memory_candidates("g1")
    )
    store.close()


def test_explicit_remember_is_accepted(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "m.db")
    writer = MemoryWriter(store)
    writer.process(
        _topic("记住我喜欢草莓蛋糕"),
        _user_targeting(),
        decision_id="d1",
        now=100,
        reply_text="好，我会记住的。",
    )
    memories = store.list_memories("g1", now=100)
    assert any("草莓蛋糕" in item.text for item in memories)
    store.close()


def test_correct_and_delete_with_tombstone_blocks_replay(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "m.db")
    store.add_memory(
        MemoryItem(
            memory_id="m-old",
            group_id="g1",
            subject_id="u1",
            kind=MemoryKind.PROFILE,
            text="我喜欢苹果",
            created_at=10,
            authority=3,
        )
    )
    corrected = store.correct_memory(
        "m-old", "我喜欢香蕉", authority=9, now=20
    )
    assert corrected is not None
    assert store.get_memory("m-old").status is MemoryStatus.SUPERSEDED
    assert store.search_memories("g1", "喜欢", now=20, limit=5)
    assert store.delete_memory(corrected.memory_id, "user_request", now=30)
    assert store.search_memories("g1", "喜欢", now=30, limit=5) == []
    assert store.has_tombstone("g1", "u1", claim_hash("我喜欢香蕉"))

    writer = MemoryWriter(store)
    writer.process(
        _topic("记住我喜欢香蕉"),
        _user_targeting(),
        decision_id="d2",
        now=40,
    )
    assert store.search_memories("g1", "香蕉", now=40, limit=5) == []
    store.close()


def test_cross_group_isolation(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "m.db")
    store.add_memory(
        MemoryItem(
            memory_id="m1",
            group_id="g1",
            subject_id="u1",
            kind=MemoryKind.PROFILE,
            text="Alice exam tomorrow",
            created_at=10,
            authority=5,
        )
    )
    assert store.search_memories("g2", "Alice exam", now=15, limit=5) == []
    store.close()


def test_scope_filter_hides_other_user_memories(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "m.db")
    store.add_memory(
        MemoryItem(
            memory_id="m1",
            group_id="g1",
            subject_id="u2",
            kind=MemoryKind.PROFILE,
            text="Bob likes hiking trails",
            created_at=10,
            authority=5,
            scope=MemoryScope.USER_IN_GROUP,
        )
    )
    found = store.search_memories(
        "g1",
        "hiking",
        now=15,
        limit=5,
        subject_ids=("u1",),
        include_user_in_group=True,
    )
    assert found == []
    blocked = store.search_memories(
        "g1",
        "hiking",
        now=15,
        limit=5,
        include_user_in_group=False,
    )
    assert blocked == []
    store.close()


def test_writer_error_does_not_block_reply(topic_snapshot, balanced_policy):
    class BoomStore(FakeMemoryRepository):
        def append_memory_candidate(self, candidate):
            raise RuntimeError("writer boom")

    platform = FakePlatform()
    memory = BoomStore()
    errors = []
    workflow = CognitiveWorkflow(
        generation_model=StaticGenerationModel("好呀。"),
        vision=NullVision(),
        platform=platform,
        memory=memory,
        persona=AemeathPersonaProvider(),
        output_guard=AemeathOutputFirewall(max_chars=60),
        rate_limiter=SlidingWindowRateLimiter(hourly_limit=6, cooldown_seconds=0),
        clock=FakeClock(),
        memory_writer=MemoryWriter(memory, on_error=errors.append),
    )
    policy = _policy()
    outcome = asyncio.run(
        workflow.evaluate(topic_snapshot, TriggerKind.ALIAS_DIRECT, policy)
    )
    assert outcome.sent is True
    # 允许异步任务稍后再失败
    async def _drain():
        await asyncio.sleep(0.05)

    asyncio.run(_drain())
    assert errors or outcome.sent is True


def test_flag_off_skips_candidates(tmp_path, topic_snapshot):
    store = SQLiteMemoryStore(tmp_path / "m.db")
    platform = FakePlatform()
    calls = {"n": 0}

    class TrackingWriter(MemoryWriter):
        def process(self, *args, **kwargs):
            calls["n"] += 1
            return super().process(*args, **kwargs)

    workflow = CognitiveWorkflow(
        generation_model=StaticGenerationModel("好呀。"),
        vision=NullVision(),
        platform=platform,
        memory=store,
        persona=AemeathPersonaProvider(),
        output_guard=AemeathOutputFirewall(max_chars=60),
        rate_limiter=SlidingWindowRateLimiter(hourly_limit=6, cooldown_seconds=0),
        clock=FakeClock(now=topic_snapshot.updated_at + 1),
        memory_writer=TrackingWriter(store),
        addressee_resolver=AddresseeResolver(),
    )
    policy = _policy(v3_memory_writer_enabled=False)
    outcome = asyncio.run(
        workflow.evaluate(topic_snapshot, TriggerKind.ALIAS_DIRECT, policy)
    )
    assert outcome.sent is True

    async def _drain():
        await asyncio.sleep(0.05)

    asyncio.run(_drain())
    assert calls["n"] == 0
    assert store.list_memory_candidates(topic_snapshot.group_id) == []
    store.close()
