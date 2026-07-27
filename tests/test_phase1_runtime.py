import asyncio

from groupmate.engine.runtime import GroupActor
from groupmate.models import GroupPolicy, WorkflowOutcome


class AsyncMemory:
    def __init__(self):
        self.messages = []

    async def save_message_async(self, message):
        self.messages.append(message)
        return True

    async def flush_async(self):
        return None


class BlockingWorkflow:
    def __init__(self):
        self.memory = AsyncMemory()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = 0
        self.triggers = []

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
    return GroupPolicy(
        aliases=("小爱",),
        debounce_min_seconds=0,
        debounce_max_seconds=0,
        spontaneous_cooldown_seconds=0,
    )


def test_ingest_continues_while_generation_is_blocked(message_factory):
    async def scenario():
        workflow = BlockingWorkflow()
        actor = GroupActor("g1", workflow, policy())
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
        actor = GroupActor("g1", workflow, policy())
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


def test_legacy_scheduler_flag_keeps_inline_behavior(message_factory):
    class ImmediateWorkflow:
        def __init__(self):
            self.evaluations = []

        async def evaluate(self, topic, trigger, policy, **kwargs):
            del topic, policy, kwargs
            self.evaluations.append(trigger.value)
            return WorkflowOutcome("legacy", False, "silent")

    async def scenario():
        workflow = ImmediateWorkflow()
        actor = GroupActor(
            "g1", workflow, policy(), v3_scheduler_enabled=False
        )
        await actor.start()
        await actor.submit(
            message_factory(message_id="legacy", text="小爱，在吗")
        )
        await actor.drain()
        snapshot = actor.snapshot()
        await actor.close()
        return workflow.evaluations, snapshot

    evaluations, snapshot = asyncio.run(scenario())
    assert evaluations == ["alias_direct"]
    assert snapshot["scheduler"] == "legacy"


def test_pause_cancels_in_flight_hard_task(message_factory):
    async def scenario():
        workflow = BlockingWorkflow()
        actor = GroupActor("g1", workflow, policy())
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
