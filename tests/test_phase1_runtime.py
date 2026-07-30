import asyncio
from inspect import signature

from groupmate.engine.runtime import GroupActor, GroupRuntimeManager
from groupmate.models import WorkflowOutcome
from groupmate.policies import BehaviorPolicy, ConversationPolicy
from tests.fakes import persona_context


class AsyncMemory:
    def __init__(self):
        self.messages = []

    async def save_message_async(self, persona_id, message):
        del persona_id
        self.messages.append(message)
        return True

    async def flush_async(self):
        return None

    def latest_open_topic_epoch(self, persona_id, group_id):
        del persona_id, group_id
        return None

    async def open_topic_epoch_async(self, *args, **kwargs):
        del args, kwargs
        return True

    async def close_topic_epoch_async(self, *args, **kwargs):
        del args, kwargs
        return True

    async def grant_continuation_async(self, **kwargs):
        del kwargs
        return True


class BlockingWorkflow:
    def __init__(self):
        self.memory = AsyncMemory()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = 0
        self.triggers = []
        self.character_name = "爱弥斯"

    async def evaluate(self, topic, trigger, policy, **kwargs):
        del topic, policy, kwargs
        self.triggers.append(trigger.value)
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return WorkflowOutcome("done-" + trigger.value, True, "sent", "在呢。")


def policy():
    return BehaviorPolicy(
        conversation=ConversationPolicy(
            debounce_min_seconds=0,
            debounce_max_seconds=0,
        )
    )


def actor_for(workflow):
    return GroupActor(
        "g1",
        workflow,
        persona_context(aliases=("小爱",)),
        policy(),
    )


def test_ingest_continues_while_generation_is_blocked(message_factory):
    async def scenario():
        workflow = BlockingWorkflow()
        actor = actor_for(workflow)
        await actor.start()
        await actor.submit(
            message_factory(message_id="wake", text="小爱，在吗", timestamp=1)
        )
        await workflow.started.wait()
        await actor.preload(
            message_factory(message_id="observed", text="后续消息", timestamp=2)
        )
        await asyncio.sleep(0)
        ids = [item.message_id for item in workflow.memory.messages]
        latest = actor.window.snapshot().latest.message_id
        await actor.close()
        return ids, latest

    ids, latest = asyncio.run(scenario())
    assert ids == ["wake", "observed"]
    assert latest == "observed"


def test_hard_trigger_cancels_running_soft_task(message_factory):
    async def scenario():
        workflow = BlockingWorkflow()
        actor = actor_for(workflow)
        await actor.start()
        await actor.submit(message_factory(message_id="soft", timestamp=1))
        await workflow.started.wait()
        await actor.submit(
            message_factory(
                message_id="hard", text="小爱，在吗", timestamp=2
            )
        )
        for _ in range(10):
            await asyncio.sleep(0)
            if workflow.cancelled:
                break
        workflow.release.set()
        await actor.drain()
        await actor.close()
        return workflow

    workflow = asyncio.run(scenario())
    assert workflow.cancelled == 1
    assert workflow.triggers == ["candidate", "alias_direct"]


def test_runtime_has_no_legacy_scheduler_switch():
    assert "v3_scheduler_enabled" not in signature(GroupActor).parameters
    assert "v3_scheduler_enabled" not in signature(GroupRuntimeManager).parameters


def test_pause_cancels_in_flight_hard_task(message_factory):
    async def scenario():
        workflow = BlockingWorkflow()
        actor = actor_for(workflow)
        await actor.start()
        await actor.submit(
            message_factory(message_id="hard", text="小爱，在吗")
        )
        await workflow.started.wait()
        await actor.submit(
            message_factory(message_id="deferred", text="还有一句")
        )
        for _ in range(10):
            await asyncio.sleep(0)
            if actor._deferred_message is not None:
                break
        actor.set_dispatch_enabled(False)
        for _ in range(10):
            await asyncio.sleep(0)
            if workflow.cancelled:
                break
        await actor.close()
        return workflow.cancelled, actor.snapshot(), actor._deferred_message

    cancelled, snapshot, deferred = asyncio.run(scenario())
    assert cancelled == 1
    assert snapshot["dispatch_enabled"] is False
    assert deferred is None
