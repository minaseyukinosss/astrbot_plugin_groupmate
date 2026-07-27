import asyncio

from groupmate.core.speak_contract import SpeakContract, is_silence
from groupmate.models import TopicSnapshot, TriggerKind
from groupmate.persona.aemeath import AemeathOutputFirewall
from groupmate.engine.rate_limit import SlidingWindowRateLimiter
from groupmate.engine.workflow import CognitiveWorkflow
from tests.fakes import (
    FakeClock,
    FakeMemoryRepository,
    FakePlatform,
    NullVision,
    StaticGenerationModel,
    StaticPersona,
)


def build_workflow(generator=None, platform=None, memory=None, clock=None):
    return CognitiveWorkflow(
        generation_model=generator or StaticGenerationModel("这也太离谱了呀。"),
        vision=NullVision(),
        platform=platform or FakePlatform(),
        memory=memory or FakeMemoryRepository(),
        persona=StaticPersona(),
        output_guard=AemeathOutputFirewall(max_chars=60),
        rate_limiter=SlidingWindowRateLimiter(hourly_limit=6, cooldown_seconds=0),
        clock=clock or FakeClock(),
    )


def test_generation_failure_fails_closed(topic_snapshot, balanced_policy):
    class Boom(StaticGenerationModel):
        async def generate(self, plan, topic, memories):
            raise RuntimeError("provider unavailable")

    platform = FakePlatform()
    workflow = build_workflow(generator=Boom("x"), platform=platform)

    outcome = asyncio.run(
        workflow.evaluate(topic_snapshot, TriggerKind.CANDIDATE, balanced_policy)
    )

    assert outcome.sent is False
    assert outcome.reason == "generation_error"
    assert platform.sent == []


def test_soft_path_generates_guards_and_sends(topic_snapshot, balanced_policy):
    platform = FakePlatform()
    memory = FakeMemoryRepository()
    workflow = build_workflow(
        generator=StaticGenerationModel("这也太离谱了呀。"),
        platform=platform,
        memory=memory,
    )

    outcome = asyncio.run(
        workflow.evaluate(topic_snapshot, TriggerKind.CANDIDATE, balanced_policy)
    )

    assert outcome.sent is True
    assert platform.sent[0]["text"] == "这也太离谱了呀。"
    assert memory.outbox[outcome.decision_id]["sent_at"] == 101
    assert any(state == "SEND" for _, _, state, _, _ in memory.transitions)


def test_soft_path_silence_does_not_send(topic_snapshot, balanced_policy):
    platform = FakePlatform()
    workflow = build_workflow(
        generator=StaticGenerationModel("<SILENCE>"),
        platform=platform,
    )

    outcome = asyncio.run(
        workflow.evaluate(topic_snapshot, TriggerKind.CANDIDATE, balanced_policy)
    )

    assert outcome.sent is False
    assert outcome.reason == "model_silence"
    assert platform.sent == []


def test_alias_direct_sends(topic_snapshot, balanced_policy):
    workflow = build_workflow()

    outcome = asyncio.run(
        workflow.evaluate(topic_snapshot, TriggerKind.ALIAS_DIRECT, balanced_policy)
    )

    assert outcome.sent is True


def test_reply_to_bot_scene_quotes_anchor_message(message_factory, balanced_policy):
    platform = FakePlatform()
    workflow = build_workflow(platform=platform)
    message = message_factory(
        message_id="reply-anchor",
        text="那这个呢",
        reply_to_bot=True,
        reply_to_message_id="bot-previous",
    )
    topic = TopicSnapshot("t1", "g1", (message,), 100, 100)

    outcome = asyncio.run(
        workflow.evaluate(topic, TriggerKind.NATIVE_DIRECT, balanced_policy)
    )

    assert outcome.sent is True
    assert platform.sent[0]["quote_message_id"] == "reply-anchor"
    assert any(
        state == "SCENE" and reason == "reply_to_bot"
        for _, _, state, reason, _ in workflow.memory.transitions
    )


def test_ambient_scene_does_not_quote_latest_message(
    topic_snapshot, balanced_policy
):
    platform = FakePlatform()
    workflow = build_workflow(platform=platform)

    outcome = asyncio.run(
        workflow.evaluate(topic_snapshot, TriggerKind.CANDIDATE, balanced_policy)
    )

    assert outcome.sent is True
    assert platform.sent[0]["quote_message_id"] is None


def test_direct_wake_is_not_rejected_as_stale_topic(message_factory, balanced_policy):
    message = message_factory(message_id="wake", text="小爱", timestamp=0)
    topic = TopicSnapshot("t1", "g1", (message,), 0, 0)
    workflow = build_workflow(clock=FakeClock(10_000))

    outcome = asyncio.run(
        workflow.evaluate(topic, TriggerKind.ALIAS_DIRECT, balanced_policy)
    )

    assert outcome.sent is True
    assert outcome.reason == "sent"


def test_continuation_sends(topic_snapshot, balanced_policy):
    workflow = build_workflow()

    outcome = asyncio.run(
        workflow.evaluate(topic_snapshot, TriggerKind.CONTINUATION, balanced_policy)
    )

    assert outcome.sent is True


def test_copied_at_sends_tip_without_llm(topic_snapshot, balanced_policy):
    platform = FakePlatform()
    memory = FakeMemoryRepository()
    generator = StaticGenerationModel("不该生成这句")
    workflow = build_workflow(
        generator=generator, platform=platform, memory=memory
    )

    outcome = asyncio.run(
        workflow.evaluate(
            topic_snapshot,
            TriggerKind.COPIED_AT,
            balanced_policy,
            trigger_alias="爱弥斯",
        )
    )

    assert outcome.sent is True
    assert outcome.reason == "copied_at_tip"
    assert platform.sent[0]["text"] == "AT爱弥斯 不能复制哦，复制的@为纯文本而非有效@"
    assert generator.calls == 0
    assert memory.outbox[outcome.decision_id]["status"] == "sent"
    assert len(memory.messages) == 1
    assert memory.messages[0].metadata["origin"] == "bot_delivery"


def test_session_remembers_assistant_turn_after_send(topic_snapshot, balanced_policy):
    workflow = build_workflow(generator=StaticGenerationModel("在呢。"))

    outcome = asyncio.run(
        workflow.evaluate(topic_snapshot, TriggerKind.ALIAS_DIRECT, balanced_policy)
    )

    assert outcome.sent is True
    turns = workflow.session_for(topic_snapshot.group_id).recent_turns()
    assert any(turn.role == "assistant" and "在呢" in turn.text for turn in turns)


def test_speak_contract_markers():
    assert is_silence("<SILENCE>")
    assert is_silence("SILENCE")
    assert not is_silence("在呢。")
    assert SpeakContract.resolve("<SILENCE>").should_send is False
    assert SpeakContract.resolve("嗨").should_send is True
