import asyncio

from groupmate.evaluation.collector import ShadowCollector
from groupmate.evaluation.shadow import HmacIdentityHasher, ShadowWorkflow
from groupmate.memory import SQLiteMemoryStore
from groupmate.models import ChatMessage, Decision, GroupPolicy, TopicSnapshot, TriggerKind
from groupmate.runtime import GroupActor
from tests.fakes import FakeClock, FailingDecisionModel, StaticDecisionModel


def topic(text="普通消息", **overrides):
    values = {
        "message_id": "m1",
        "group_id": "real-group",
        "sender_id": "real-user",
        "sender_name": "真实昵称",
        "text": text,
        "timestamp": 100,
    }
    values.update(overrides)
    message = ChatMessage(**values)
    return TopicSnapshot("t", "real-group", (message,), 100, 100)


def build_workflow(tmp_path, model=None, store_text=False, sample_rate=1.0):
    memory = SQLiteMemoryStore(tmp_path / "memory.db")
    workflow = ShadowWorkflow(
        decision_model=model or StaticDecisionModel(Decision.ignore("not_useful")),
        memory=memory,
        collector=ShadowCollector(store_text=store_text),
        hasher=HmacIdentityHasher(tmp_path / "shadow.key"),
        clock=FakeClock(100),
        model_id="model-a",
        retention_days=7,
        sample_rate=sample_rate,
    )
    return workflow, memory


def test_hmac_hasher_is_stable_and_does_not_expose_identity(tmp_path):
    hasher = HmacIdentityHasher(tmp_path / "shadow.key")
    first = hasher.digest("123456")
    second = HmacIdentityHasher(tmp_path / "shadow.key").digest("123456")
    assert first == second
    assert "123456" not in first
    assert len(first) == 64


def test_shadow_workflow_records_decision_without_generation_or_send(tmp_path):
    workflow, memory = build_workflow(
        tmp_path,
        StaticDecisionModel(
            Decision.respond("可以补充", confidence=0.9, reason_code="useful")
        ),
        store_text=True,
    )
    outcome = asyncio.run(
        workflow.evaluate(topic(), TriggerKind.CANDIDATE, GroupPolicy())
    )
    assert outcome.sent is False
    assert outcome.reason == "shadow_recorded"
    row = memory.get_shadow_decision(outcome.decision_id)
    assert row["action"] == "respond"
    assert row["group_hash"] != "real-group"
    assert "real-user" not in (row["context_json"] or "")
    memory.close()


def test_shadow_model_error_is_recorded_as_safe_silence(tmp_path):
    workflow, memory = build_workflow(tmp_path, FailingDecisionModel())
    outcome = asyncio.run(
        workflow.evaluate(topic(), TriggerKind.CANDIDATE, GroupPolicy())
    )
    row = memory.get_shadow_decision(outcome.decision_id)
    assert row["action"] == "ignore"
    assert row["error_code"] == "decision_error"
    memory.close()


def test_zero_sample_rate_writes_nothing(tmp_path):
    workflow, memory = build_workflow(tmp_path, sample_rate=0.0)
    outcome = asyncio.run(
        workflow.evaluate(topic(), TriggerKind.CANDIDATE, GroupPolicy())
    )
    assert outcome.reason == "shadow_not_sampled"
    assert memory.shadow_count() == 0
    memory.close()


def test_actor_records_command_and_native_bypasses(tmp_path):
    workflow, memory = build_workflow(tmp_path)

    async def scenario():
        actor = GroupActor(
            "real-group",
            workflow,
            GroupPolicy(debounce_min_seconds=0, debounce_max_seconds=0),
        )
        await actor.submit(topic("/help", is_command=True).latest)
        await actor.submit(topic("在吗", message_id="m2", mentions_bot=True).latest)
        await actor.drain()
        await actor.close()

    asyncio.run(scenario())
    stats = memory.shadow_stats()
    assert stats["total"] == 2
    assert stats["reasons"]["existing_command"] == 1
    assert stats["reasons"]["native_direct"] == 1
    memory.close()
