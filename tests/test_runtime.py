import asyncio

from groupmate.models import GroupPolicy
from groupmate.runtime import GroupActor, GroupRuntimeManager
from tests.fakes import RecordingWorkflow


def fast_policy():
    return GroupPolicy(
        aliases=("小爱",),
        debounce_min_seconds=0.01,
        debounce_max_seconds=0.01,
        spontaneous_cooldown_seconds=0,
    )


def test_burst_collapses_to_one_evaluation(message_factory):
    async def scenario():
        workflow = RecordingWorkflow()
        actor = GroupActor("g1", workflow, policy=fast_policy())
        await actor.start()
        for index in range(4):
            await actor.submit(
                message_factory(message_id=str(index), timestamp=100 + index)
            )
        await actor.drain()
        await actor.close()
        return workflow

    workflow = asyncio.run(scenario())

    assert len(workflow.evaluations) == 1
    assert workflow.evaluations[0][0].latest.message_id == "3"


def test_native_wake_cancels_pending_spontaneous_topic(message_factory):
    async def scenario():
        workflow = RecordingWorkflow()
        actor = GroupActor("g1", workflow, policy=fast_policy())
        await actor.start()
        await actor.submit(message_factory(message_id="soft"))
        await actor.submit(message_factory(message_id="direct", mentions_bot=True))
        await actor.drain()
        await actor.close()
        return workflow

    workflow = asyncio.run(scenario())

    assert workflow.evaluations == []


def test_alias_direct_is_evaluated_without_debounce(message_factory):
    async def scenario():
        workflow = RecordingWorkflow()
        actor = GroupActor("g1", workflow, policy=fast_policy())
        await actor.start()
        await actor.submit(message_factory(message_id="direct", text="小爱，在吗"))
        await actor.drain()
        await actor.close()
        return workflow

    workflow = asyncio.run(scenario())

    assert len(workflow.evaluations) == 1
    assert workflow.evaluations[0][1].value == "alias_direct"


def test_runtime_manager_keeps_groups_isolated(message_factory):
    async def scenario():
        workflows = {}

        def factory(group_id):
            workflows[group_id] = RecordingWorkflow()
            return workflows[group_id]

        manager = GroupRuntimeManager(factory, lambda group_id: fast_policy())
        await manager.submit(message_factory(group_id="g1", message_id="1"))
        await manager.submit(message_factory(group_id="g2", message_id="2"))
        await manager.drain()
        await manager.close()
        return workflows

    workflows = asyncio.run(scenario())

    assert set(workflows) == {"g1", "g2"}
    assert workflows["g1"].evaluations[0][0].group_id == "g1"
    assert workflows["g2"].evaluations[0][0].group_id == "g2"

