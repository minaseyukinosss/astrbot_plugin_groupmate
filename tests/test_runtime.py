import asyncio

from groupmate.engine.runtime import GroupActor, GroupRuntimeManager
from groupmate.policies import BehaviorPolicy, ConversationPolicy, ReplyPolicy, ResourcePolicy
from tests.fakes import RecordingWorkflow, persona_context


def persona():
    return persona_context(aliases=("小爱",))


def future_persona():
    return persona_context(
        aliases=("新人格",),
        persona_id="future",
        display_name="新人格",
    )


def fast_policy(**overrides):
    conversation = {
        "debounce_min_seconds": 0.01,
        "debounce_max_seconds": 0.01,
    }
    conversation.update(overrides)
    return BehaviorPolicy(
        conversation=ConversationPolicy(**conversation),
        reply=ReplyPolicy(humanize_delay_enabled=False),
        resources=ResourcePolicy(open_send_cooldown_seconds=0),
    )


def actor_for(workflow, behavior=None):
    return GroupActor("g1", workflow, persona(), behavior or fast_policy())


def test_topic_max_seconds_clamps_debounce_wait(message_factory):
    async def scenario():
        workflow = RecordingWorkflow()
        policy = fast_policy(
            debounce_min_seconds=5.0,
            debounce_max_seconds=5.0,
            topic_max_seconds=12,
        )
        actor = actor_for(workflow, policy)
        await actor.start()
        await actor.submit(message_factory(message_id="1", timestamp=100, text="今天好热啊大家"))
        await actor.submit(
            message_factory(message_id="2", timestamp=111, text="空调都没用")
        )
        await actor.drain()
        await actor.close()
        return workflow

    workflow = asyncio.run(scenario())
    assert len(workflow.evaluations) == 1
    assert workflow.evaluations[0][0].latest.message_id == "2"


def test_debounce_collapses_message_burst(message_factory):
    async def scenario():
        workflow = RecordingWorkflow()
        actor = actor_for(workflow)
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
        actor = actor_for(workflow)
        await actor.start()
        await actor.submit(message_factory(message_id="soft"))
        await actor.submit(message_factory(message_id="direct", mentions_bot=True))
        await actor.drain()
        await actor.close()
        return workflow

    workflow = asyncio.run(scenario())

    assert len(workflow.evaluations) == 1
    assert workflow.evaluations[0][1].value == "native_direct"
    assert workflow.evaluations[0][0].latest.message_id == "direct"


def test_native_wake_always_uses_unified_runtime(message_factory):
    async def scenario():
        workflow = RecordingWorkflow()
        actor = actor_for(workflow)
        await actor.start()
        await actor.submit(message_factory(message_id="soft"))
        await actor.submit(message_factory(message_id="direct", mentions_bot=True))
        await actor.drain()
        await actor.close()
        return workflow

    workflow = asyncio.run(scenario())

    assert [item[1].value for item in workflow.evaluations] == ["native_direct"]


def test_alias_direct_is_evaluated_without_debounce(message_factory):
    async def scenario():
        workflow = RecordingWorkflow()
        actor = actor_for(workflow)
        await actor.start()
        await actor.submit(message_factory(message_id="direct", text="小爱，在吗"))
        await actor.drain()
        await actor.close()
        return workflow

    workflow = asyncio.run(scenario())

    assert len(workflow.evaluations) == 1
    assert workflow.evaluations[0][1].value == "alias_direct"


def test_followup_after_direct_wake_uses_continuation(message_factory):
    async def scenario():
        workflow = RecordingWorkflow()
        actor = actor_for(workflow, fast_policy(continuation_seconds=90))
        await actor.start()
        await actor.submit(
            message_factory(message_id="wake", text="小爱", timestamp=100)
        )
        await actor.submit(
            message_factory(
                message_id="follow",
                text="你在干嘛呢",
                timestamp=105,
            )
        )
        await actor.drain()
        await actor.close()
        return workflow, actor

    workflow, actor = asyncio.run(scenario())

    assert [item[1].value for item in workflow.evaluations] == [
        "alias_direct",
        "continuation",
    ]
    assert actor.snapshot()["continuation_active"] is True


def test_followup_from_other_sender_stays_candidate(message_factory):
    async def scenario():
        workflow = RecordingWorkflow()
        actor = actor_for(workflow, fast_policy(continuation_seconds=90))
        await actor.start()
        await actor.submit(
            message_factory(message_id="wake", text="小爱", timestamp=100)
        )
        await actor.submit(
            message_factory(
                message_id="other",
                sender_id="u2",
                sender_name="Bob",
                text="你在干嘛呢",
                timestamp=105,
            )
        )
        await actor.drain()
        await actor.close()
        return workflow

    workflow = asyncio.run(scenario())

    assert [item[1].value for item in workflow.evaluations] == [
        "alias_direct",
        "candidate",
    ]


def test_duplicate_direct_wake_still_evaluates_after_preload(message_factory):
    async def scenario():
        workflow = RecordingWorkflow()
        actor = actor_for(workflow)
        await actor.start()
        message = message_factory(message_id="direct", text="小爱，在吗")
        await actor.preload(message)
        await actor.submit(message)
        await actor.drain()
        await actor.close()
        return workflow

    workflow = asyncio.run(scenario())

    assert len(workflow.evaluations) == 1
    assert workflow.evaluations[0][1].value == "alias_direct"


def test_runtime_manager_keeps_groups_isolated(message_factory):
    async def scenario():
        workflows = {}

        def factory(group_id, persona_context):
            del persona_context
            workflows[group_id] = RecordingWorkflow()
            return workflows[group_id]

        manager = GroupRuntimeManager(
            factory,
            lambda group_id: persona(),
            lambda group_id: fast_policy(),
        )
        await manager.submit(message_factory(group_id="g1", message_id="1"))
        await manager.submit(message_factory(group_id="g2", message_id="2"))
        await manager.drain()
        await manager.close()
        return workflows

    workflows = asyncio.run(scenario())

    assert set(workflows) == {"g1", "g2"}
    assert workflows["g1"].evaluations[0][0].group_id == "g1"
    assert workflows["g2"].evaluations[0][0].group_id == "g2"


def test_runtime_manager_keeps_personas_isolated_for_same_group():
    async def scenario():
        workflows = {}

        def factory(group_id, persona_context):
            workflow = RecordingWorkflow()
            workflows.setdefault(group_id, []).append(
                (persona_context.persona_id, workflow)
            )
            return workflow

        manager = GroupRuntimeManager(
            factory,
            lambda group_id: persona(),
            lambda group_id: fast_policy(),
        )
        aemeath = await manager.actor_for("g1", persona())
        future = await manager.actor_for("g1", future_persona())
        repeat = await manager.actor_for("g1", persona())
        await manager.close()
        return aemeath, future, repeat, workflows

    aemeath, future, repeat, workflows = asyncio.run(scenario())

    assert aemeath is repeat
    assert future is not aemeath
    assert future.window.snapshot().messages == ()
    assert [item[0] for item in workflows["g1"]] == ["aemeath", "future"]


def test_direct_requests_are_serialized_without_invalidating_first(message_factory):
    class BlockingWorkflow:
        def __init__(self):
            self.started = []
            self.completed = []
            self.first_started = asyncio.Event()
            self.release_first = asyncio.Event()

        async def evaluate(
            self, topic, trigger, policy, trigger_alias="", still_valid=None
        ):
            del trigger, policy, trigger_alias
            message_id = topic.latest.message_id
            self.started.append(message_id)
            if message_id == "first":
                self.first_started.set()
                await self.release_first.wait()
            assert still_valid is None or still_valid()
            self.completed.append(message_id)
            from groupmate.models import WorkflowOutcome

            return WorkflowOutcome("d-" + message_id, True, "sent", message_id)

    async def scenario():
        workflow = BlockingWorkflow()
        actor = actor_for(workflow)
        await actor.start()
        await actor.submit(
            message_factory(message_id="first", text="小爱，先回答我")
        )
        await workflow.first_started.wait()
        await actor.submit(
            message_factory(
                message_id="second",
                sender_id="u2",
                sender_name="Bob",
                text="小爱，我也问一个",
                timestamp=101,
            )
        )
        await asyncio.sleep(0)
        started_before_release = list(workflow.started)
        workflow.release_first.set()
        await actor.drain()
        await actor.close()
        return workflow, started_before_release

    workflow, started_before_release = asyncio.run(scenario())

    assert started_before_release == ["first"]
    assert workflow.completed == ["first", "second"]


def test_continuation_grants_are_kept_per_sender(message_factory):
    async def scenario():
        workflow = RecordingWorkflow()
        actor = actor_for(workflow, fast_policy(continuation_seconds=90))
        await actor.start()
        await actor.submit(
            message_factory(message_id="u1-wake", text="小爱", timestamp=100)
        )
        await actor.drain()
        await actor.submit(
            message_factory(
                message_id="u2-wake",
                sender_id="u2",
                sender_name="Bob",
                text="小爱",
                timestamp=101,
            )
        )
        await actor.drain()
        await actor.submit(
            message_factory(
                message_id="u1-follow",
                text="那第二种呢",
                timestamp=105,
            )
        )
        await actor.drain()
        await actor.close()
        return [item[1].value for item in workflow.evaluations]

    assert asyncio.run(scenario()) == [
        "alias_direct",
        "alias_direct",
        "continuation",
    ]
