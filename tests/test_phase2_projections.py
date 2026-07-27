"""Phase 2 projection rebuild and continuation grant tests."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from groupmate.core.projections import StateProjector
from groupmate.engine.rate_limit import SlidingWindowRateLimiter
from groupmate.engine.runtime import GroupActor
from groupmate.engine.topics import TopicWindow
from groupmate.engine.workflow import CognitiveWorkflow
from groupmate.memory.store import SQLiteMemoryStore
from groupmate.models import (
    ChatMessage,
    GroupPolicy,
    MessageOrigin,
    TriggerKind,
    WorkflowOutcome,
)
from tests.fakes import (
    FakeClock,
    FakeMemoryRepository,
    FakePlatform,
    NullVision,
    StaticGenerationModel,
    StaticPersona,
)


def _policy(**kwargs):
    base = dict(
        aliases=("小爱",),
        continuation_seconds=90,
        debounce_min_seconds=0,
        debounce_max_seconds=0,
        spontaneous_cooldown_seconds=0,
        humanize_delay_enabled=False,
        history_limit=50,
    )
    base.update(kwargs)
    return GroupPolicy(**base)


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
    assert store.save_message(user)
    assert store.save_message(bot)
    topic_id = uuid4().hex
    store.open_topic_epoch("g1", topic_id, now - 10, "u1")
    store.grant_continuation(
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
        "cand-1",
        "g1",
        "哈哈",
        created_at=now - 5,
        kind="candidate",
    )
    store.mark_outbox_sent("cand-1", sent_at=now - 5)

    projector = StateProjector(store)
    snapshot = projector.rebuild("g1", now=now, policy=_policy())
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


def test_continuation_reply_does_not_renew_grant(tmp_path, message_factory):
    async def scenario():
        store = SQLiteMemoryStore(tmp_path / "grant.db")
        platform = FakePlatform()
        workflow = CognitiveWorkflow(
            generation_model=StaticGenerationModel("好的。"),
            vision=NullVision(),
            platform=platform,
            memory=store,
            persona=StaticPersona(),
            output_guard=__import__(
                "groupmate.persona.aemeath", fromlist=["AemeathOutputFirewall"]
            ).AemeathOutputFirewall(60),
            rate_limiter=SlidingWindowRateLimiter(6, 0),
            clock=FakeClock(200),
            character_name="爱弥斯",
        )
        actor = GroupActor("g1", workflow, _policy())
        await actor.start()
        await actor.submit(
            message_factory(
                message_id="wake",
                text="小爱",
                timestamp=100,
            )
        )
        await actor.drain()
        first = store.latest_continuation_grant("g1", now=150)
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
        second = store.latest_continuation_grant("g1", now=150)
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

        actor = GroupActor("g1", Recording(), _policy())
        await actor.start()
        await actor.preload(
            message_factory(message_id="h1", text="历史消息", timestamp=1)
        )
        await actor.drain()
        await actor.close()
        return evaluations

    assert asyncio.run(scenario()) == []


def test_unknown_outbox_not_counted_in_rate_rebuild(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "unknown.db")
    store.enqueue_outbox("u1", "g", "x", created_at=10, kind="candidate")
    asyncio.run(
        store.transition_outbox_async(
            "u1", "pending", "sending", increment_attempt=True
        )
    )
    asyncio.run(
        store.transition_outbox_async(
            "u1", "sending", "unknown", failure_code="no_receipt"
        )
    )
    assert store.list_spontaneous_sent_at("g", since=0) == []
    store.close()
