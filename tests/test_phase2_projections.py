"""Phase 2 projection rebuild and continuation grant tests."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from groupmate.core.projections import StateProjector
from groupmate.core.session import GroupSession
from groupmate.engine.rate_limit import SlidingWindowRateLimiter
from groupmate.engine.runtime import GroupActor
from groupmate.engine.topics import TopicWindow
from groupmate.engine.workflow import CognitiveWorkflow
from groupmate.memory.store import SQLiteMemoryStore
from groupmate.models import (
    ChatMessage,
    MessageOrigin,
    TriggerKind,
    WorkflowOutcome,
)
from groupmate.policies import BehaviorPolicy, ConversationPolicy, ReplyPolicy, ResourcePolicy
from tests.fakes import (
    FakeClock,
    FakeMemoryRepository,
    FakePlatform,
    NullVision,
    StaticGenerationModel,
    StaticPersona,
    persona_context,
)


def _policy(**kwargs):
    base = dict(
        continuation_seconds=90,
        debounce_min_seconds=0,
        debounce_max_seconds=0,
        history_limit=50,
    )
    base.update(kwargs)
    return BehaviorPolicy(
        conversation=ConversationPolicy(**base),
        reply=ReplyPolicy(humanize_delay_enabled=False),
        resources=ResourcePolicy(open_send_cooldown_seconds=0),
    )


def test_rebuild_restores_topic_session_continuation_and_outputs(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "proj.db")
    now = 1_700_000_100
    user = ChatMessage(
        message_id="u1",
        group_id="g1",
        sender_id="u1",
        sender_name="Alice",
        text="小爱",
        timestamp=now - 10,
        origin=MessageOrigin.PLATFORM_REALTIME,
        ingested_at=now - 10,
    )
    bot = ChatMessage(
        message_id="bot-d1",
        group_id="g1",
        sender_id="__bot__",
        sender_name="爱弥斯",
        text="在呢。",
        timestamp=now - 9,
        is_bot=True,
        origin=MessageOrigin.BOT_DELIVERY,
        decision_id="d1",
        ingested_at=now - 9,
    )
    assert store.save_message("aemeath", user)
    assert store.save_message("aemeath", bot)
    topic_id = uuid4().hex
    store.open_topic_epoch("aemeath", "g1", topic_id, now - 10, "u1")
    store.grant_continuation(
        persona_id="aemeath",
        grant_id="grant-1",
        group_id="g1",
        sender_id="u1",
        opened_by_decision_id="d1",
        opened_by_message_id="u1",
        trigger_kind="NATIVE_DIRECT",
        granted_at=now - 9,
        expires_at=now + 80,
        max_total_seconds=90,
    )
    store.enqueue_outbox(
        "aemeath",
        "cand-1",
        "g1",
        "哈哈",
        created_at=now - 5,
        kind="candidate",
    )
    store.mark_outbox_sent("aemeath", "cand-1", sent_at=now - 5)

    projector = StateProjector(store)
    snapshot = projector.rebuild(
        "aemeath", "g1", now=now, policy=_policy().conversation
    )
    assert snapshot.topic_id == topic_id
    assert [item.message_id for item in snapshot.messages] == ["u1", "bot-d1"]
    assert snapshot.continuation is not None
    assert snapshot.continuation.sender_id == "u1"
    assert snapshot.continuation.expires_at == now + 80
    assert "在呢。" in snapshot.recent_outputs
    assert now - 5 in snapshot.spontaneous_sent_at
    assert any(turn.role == "assistant" for turn in snapshot.session_turns)

    window = TopicWindow("g1")
    session = __import__(
        "groupmate.core.session", fromlist=["GroupSession"]
    ).GroupSession("g1")
    limiter = SlidingWindowRateLimiter(6, 0)

    class WorkflowStub:
        def __init__(self):
            self.outputs = []

        def hydrate_recent_outputs(self, group_id, texts):
            self.outputs = list(texts)

    workflow = WorkflowStub()
    holder = {"sender": "", "until": 0}

    def set_continuation(sender_id, expires_at):
        holder["sender"] = sender_id
        holder["until"] = expires_at

    projector.apply(
        snapshot,
        window=window,
        session=session,
        rate_limiter=limiter,
        workflow=workflow,
        set_continuation=set_continuation,
    )
    assert window.snapshot().topic_id == topic_id
    assert window.snapshot().created_at == now - 10
    assert holder["sender"] == "u1"
    assert holder["until"] == now + 80
    assert workflow.outputs[-1] == "在呢。"
    assert limiter.allow(now) is True
    store.close()


def test_projection_does_not_restore_decorative_media_ids(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "media-projection.db")
    bot = ChatMessage(
        message_id="bot-rich",
        group_id="g1",
        sender_id="__bot__",
        sender_name="爱弥斯",
        text="给你看",
        timestamp=100,
        is_bot=True,
        image_urls=("https://example.test/result.png",),
        segment_types=("text", "image"),
        origin=MessageOrigin.BOT_DELIVERY,
        decision_id="rich",
        ingested_at=100,
        metadata={
            "origin": "bot_delivery",
            "decision_id": "rich",
            "media_ids": ["warm-1", "result-1"],
        },
    )
    assert store.save_message("aemeath", bot)
    snapshot = StateProjector(store).rebuild(
        "aemeath", "g1", now=200, policy=_policy().conversation
    )

    class WorkflowStub:
        def hydrate_recent_media_ids(self, group_id, media_ids):
            raise AssertionError("decorative media state must not be hydrated")

    workflow = WorkflowStub()
    StateProjector(store).apply(
        snapshot,
        window=TopicWindow("g1"),
        session=GroupSession("g1"),
        rate_limiter=SlidingWindowRateLimiter(6, 0),
        workflow=workflow,
        set_continuation=lambda sender_id, expires_at: None,
    )

    assert not hasattr(snapshot, "recent_media_ids")
    store.close()


def test_continuation_reply_does_not_renew_grant(tmp_path, message_factory):
    async def scenario():
        store = SQLiteMemoryStore(tmp_path / "grant.db")
        platform = FakePlatform()
        workflow = CognitiveWorkflow(
            generation_model=StaticGenerationModel("好的。"),
            vision=NullVision(),
            platform=platform,
            memory=store,
            persona_context=persona_context(StaticPersona(), aliases=("小爱",)),
            behavior=_policy(),
            vision_enabled=True,
            output_guard=__import__(
                "groupmate.persona.aemeath", fromlist=["AemeathOutputFirewall"]
            ).AemeathOutputFirewall(),
            rate_limiter=SlidingWindowRateLimiter(6, 0),
            clock=FakeClock(200),
            character_name="爱弥斯",
        )
        actor = GroupActor(
            "g1", workflow, persona_context(aliases=("小爱",)), _policy()
        )
        await actor.start()
        await actor.submit(
            message_factory(
                message_id="wake",
                text="小爱",
                timestamp=100,
            )
        )
        await actor.drain()
        first = store.latest_continuation_grant("aemeath", "g1", now=150)
        assert first is not None
        first_expires = int(first["expires_at"])
        await actor.submit(
            message_factory(
                message_id="follow",
                text="你在干嘛呢",
                timestamp=105,
            )
        )
        await actor.drain()
        second = store.latest_continuation_grant("aemeath", "g1", now=150)
        await actor.close()
        store.close()
        return first_expires, second, [item[1].value for item in []]

    first_expires, second, _ = asyncio.run(scenario())
    assert second is not None
    assert int(second["expires_at"]) == first_expires


def test_history_preload_does_not_schedule(message_factory, tmp_path):
    async def scenario():
        evaluations = []

        class Recording:
            memory = FakeMemoryRepository()

            async def evaluate(self, topic, trigger, policy, **kwargs):
                evaluations.append(trigger)
                return WorkflowOutcome("x", False, "silent")

        actor = GroupActor(
            "g1", Recording(), persona_context(aliases=("小爱",)), _policy()
        )
        await actor.start()
        await actor.preload(
            message_factory(message_id="h1", text="历史消息", timestamp=1)
        )
        await actor.drain()
        await actor.close()
        return evaluations

    assert asyncio.run(scenario()) == []


def test_rebuild_restores_active_continuations_for_each_sender(tmp_path):
    now = 200
    store = SQLiteMemoryStore(tmp_path / "multi-grants.db")
    for sender_id, granted_at in (("u1", 100), ("u2", 110)):
        store.grant_continuation(
            persona_id="aemeath",
            grant_id="grant-" + sender_id,
            group_id="g1",
            sender_id=sender_id,
            opened_by_decision_id="d-" + sender_id,
            opened_by_message_id="m-" + sender_id,
            trigger_kind="ALIAS_DIRECT",
            granted_at=granted_at,
            expires_at=now + 60,
            max_total_seconds=200,
        )

    snapshot = StateProjector(store).rebuild(
        "aemeath", "g1", now=now, policy=_policy().conversation
    )
    restored = {}

    def set_continuation(sender_id, expires_at):
        if not sender_id:
            restored.clear()
        else:
            restored[sender_id] = expires_at

    StateProjector(store).apply(
        snapshot,
        window=TopicWindow("g1"),
        session=GroupSession("g1"),
        rate_limiter=SlidingWindowRateLimiter(6, 0),
        workflow=type("Workflow", (), {})(),
        set_continuation=set_continuation,
    )
    store.close()

    assert set(restored) == {"u1", "u2"}


def test_unknown_outbox_not_counted_in_rate_rebuild(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "unknown.db")
    store.enqueue_outbox(
        "aemeath", "u1", "g", "x", created_at=10, kind="candidate"
    )
    asyncio.run(
        store.transition_outbox_async(
            "aemeath", "u1", "pending", "sending", increment_attempt=True
        )
    )
    asyncio.run(
        store.transition_outbox_async(
            "aemeath", "u1", "sending", "unknown", failure_code="no_receipt"
        )
    )
    assert store.list_spontaneous_sent_at("aemeath", "g", since=0) == []
    store.close()
