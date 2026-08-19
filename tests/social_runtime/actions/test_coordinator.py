from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from groupmate.social_runtime.actions.contracts import (
    ActionEdge,
    ActionNode,
    ActionPlan,
    DeliveryBundle,
    DeliveryPart,
    DeliveryPartKind,
    DeliveryReceipt,
    DeliveryReceiptStatus,
    PlanContext,
)
from groupmate.social_runtime.actions.coordinator import (
    ExecutionCoordinator,
    NodeExecutionResult,
    NodeExecutionStatus,
    PlanNotValidated,
)
from groupmate.social_runtime.delivery.outbox import OutboxService
from groupmate.social_runtime.persistence.event_store import SQLiteSocialEventStore
from groupmate.social_runtime.tasks.contracts import (
    CapabilityDescriptor,
    CapabilityField,
    CapabilityRequest,
    ConfirmationPolicy,
    ProviderEvent,
    RiskLevel,
)
from groupmate.social_runtime.tasks.runtime import TaskRuntime
from groupmate.social_runtime.validator import ActionPlanValidator


NODE_KINDS = (
    "GENERATE_TEXT",
    "SELECT_MEDIA",
    "REQUEST_CONFIRMATION",
    "CALL_CAPABILITY",
    "RENDER_PROGRESS",
    "RENDER_RESULT",
)


def _context(**overrides):
    values = {
        "now": 100,
        "group_id": "group-1",
        "persona_id": "persona-1",
        "scene_version": 3,
        "config_version": 7,
        "persona_version": 11,
        "constitution_version": 13,
        "relationship_version": 17,
        "state_version": 19,
        "requester_permissions": tuple(f"run:{kind}" for kind in NODE_KINDS),
        "supported_node_kinds": NODE_KINDS,
        "allowed_audience_ids": ("user-1",),
        "allowed_owner_ids": ("coordinator-worker",),
        "max_nodes": 24,
        "max_plan_duration": 60,
        "max_retries": 2,
        "max_autonomous_followups": 0,
        "constitution_allowed": True,
        "relationship_allowed": True,
        "state_allowed": True,
        "max_risk_score": 5,
        "allowed_media_references": (),
        "max_budget_cost": 10,
        "max_concurrency": 3,
        "confirmed_ids": (),
    }
    values.update(overrides)
    return PlanContext(**values)


def _node(node_id, kind, *, visible=False):
    return ActionNode(
        node_id=node_id,
        kind=kind,
        owner_id="coordinator-worker",
        retry_limit=0,
        deadline_at=150,
        permission=f"run:{kind}",
        visible=visible,
    )


def _plan(**overrides):
    nodes = (
        _node("text", "GENERATE_TEXT"),
        _node("media", "SELECT_MEDIA"),
        _node("confirmation", "REQUEST_CONFIRMATION"),
        _node("capability", "CALL_CAPABILITY"),
        _node("progress", "RENDER_PROGRESS"),
        _node("result", "RENDER_RESULT", visible=True),
    )
    values = {
        "plan_id": "plan-1",
        "correlation_id": "corr-1",
        "group_id": "group-1",
        "persona_id": "persona-1",
        "scene_version": 3,
        "config_version": 7,
        "persona_version": 11,
        "constitution_version": 13,
        "relationship_version": 17,
        "state_version": 19,
        "intention_ids": ("intention-1",),
        "audience": ("user-1",),
        "topic_id": "topic-1",
        "origin": "direct_request",
        "nodes": nodes,
        "edges": (
            ActionEdge("text", "capability"),
            ActionEdge("media", "capability"),
            ActionEdge("confirmation", "capability"),
            ActionEdge("capability", "progress"),
            ActionEdge("capability", "result"),
        ),
        "constraints": ("governor_act",),
        "constitution_approved": True,
        "relationship_approved": True,
        "state_approved": True,
        "risk_score": 0,
        "media_references": (),
        "budget_cost": 0,
        "concurrency": 3,
        "confirmation_ids": (),
        "expires_at": 160,
    }
    values.update(overrides)
    return ActionPlan(**values)


def _validation(plan):
    return ActionPlanValidator().validate(plan, _context())


class ConcurrentRecorder:
    def __init__(self, gated_kinds=()):
        self.calls = []
        self.active = 0
        self.max_active = 0
        self.gated_kinds = frozenset(gated_kinds)
        self.entered = {kind: asyncio.Event() for kind in self.gated_kinds}
        self.release = asyncio.Event()

    async def __call__(self, plan, node, dependencies, now):
        del plan, now
        self.calls.append((node.node_id, tuple(sorted(dependencies))))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if node.kind in self.gated_kinds:
                self.entered[node.kind].set()
                await self.release.wait()
            return NodeExecutionResult.succeeded({"node": node.node_id})
        finally:
            self.active -= 1


def test_coordinator_rejects_unvalidated_or_differently_validated_plan(tmp_path):
    plan = _plan()
    validation = _validation(plan)
    coordinator = ExecutionCoordinator(tmp_path / "groupmate-social-runtime-v2.db")

    with pytest.raises(PlanNotValidated):
        coordinator.submit(replace(plan, correlation_id="changed"), validation, now=100)

    rejected = ActionPlanValidator().validate(
        replace(plan, scene_version=2), _context()
    )
    with pytest.raises(PlanNotValidated):
        coordinator.submit(plan, rejected, now=100)


def test_dag_roots_run_concurrently_and_restart_resumes_only_next_nodes(tmp_path):
    async def scenario():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        plan = _plan()
        recorder = ConcurrentRecorder(
            ("GENERATE_TEXT", "SELECT_MEDIA", "REQUEST_CONFIRMATION")
        )
        executors = {kind: recorder for kind in NODE_KINDS}
        first = ExecutionCoordinator(path, executors=executors)
        submitted = first.submit(plan, _validation(plan), now=100)
        assert submitted.runnable_node_ids == ("confirmation", "media", "text")

        advancing = asyncio.create_task(first.advance(plan.plan_id, now=101))
        await asyncio.gather(*(event.wait() for event in recorder.entered.values()))
        calls_while_blocked = tuple(recorder.calls)
        recorder.release.set()
        after_roots = await advancing

        restarted_recorder = ConcurrentRecorder()
        restarted = ExecutionCoordinator(
            path,
            executors={kind: restarted_recorder for kind in NODE_KINDS},
        )
        recovered = restarted.load(plan.plan_id)
        after_capability = await restarted.advance(plan.plan_id, now=102)
        completed = await restarted.advance(plan.plan_id, now=103)
        return (
            calls_while_blocked,
            recorder.max_active,
            after_roots,
            recovered,
            after_capability,
            completed,
            restarted_recorder,
        )

    (
        calls_while_blocked,
        max_active,
        after_roots,
        recovered,
        after_capability,
        completed,
        restarted_recorder,
    ) = asyncio.run(scenario())

    assert {node_id for node_id, _ in calls_while_blocked} == {
        "text",
        "media",
        "confirmation",
    }
    assert max_active == 3
    assert after_roots.runnable_node_ids == ("capability",)
    assert recovered == after_roots
    assert after_capability.runnable_node_ids == ("progress", "result")
    assert completed.status == "completed"
    assert all(
        state.status is NodeExecutionStatus.SUCCEEDED
        for state in completed.node_states
    )
    assert [node_id for node_id, _ in restarted_recorder.calls] == [
        "capability",
        "progress",
        "result",
    ]


def _task_runtime(path):
    runtime = TaskRuntime(path)
    descriptor = CapabilityDescriptor.create(
        capability_id="lookup.weather",
        provider_id="provider.weather",
        input_schema=(CapabilityField("city", "string"),),
        output_schema=(CapabilityField("forecast", "string"),),
        risk_level=RiskLevel.READ_ONLY,
        required_scopes=("weather.read",),
        idempotent=True,
        cancellable=False,
        supports_progress=True,
        expected_latency_ms=5000,
        media_output_kinds=(),
        confirmation_policy=ConfirmationPolicy.NEVER,
    )
    task = runtime.propose(
        descriptor,
        CapabilityRequest.create(
            requester_id="user-1",
            persona_id="persona-1",
            group_id="group-1",
            topic_id="topic-1",
            input_payload={"city": "上海"},
            authorization_scopes=("weather.read",),
            idempotency_key="weather-1",
            correlation_id="corr-task-1",
            expires_at=200,
            direct_request=True,
        ),
        now=100,
    )
    task = runtime.start(task.task_id, now=101)
    task = runtime.start(task.task_id, now=102)
    return runtime, task


def test_progress_and_result_feedback_come_from_structured_provider_events(tmp_path):
    async def scenario():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        runtime, task = _task_runtime(path)
        feedback = []

        async def sink(event):
            feedback.append(event)

        coordinator = ExecutionCoordinator(
            path,
            task_runtime=runtime,
            event_sink=sink,
        )
        before = tuple(feedback)
        progressed = await coordinator.apply_provider_event(
            ProviderEvent.create(
                event_id="provider:progress:1",
                task_id=task.task_id,
                kind="progress",
                occurred_at=110,
                progress=40,
            )
        )
        succeeded = await coordinator.apply_provider_event(
            ProviderEvent.create(
                event_id="provider:result:1",
                task_id=task.task_id,
                kind="succeeded",
                occurred_at=120,
                result={"forecast": "晴"},
            )
        )
        return before, progressed, succeeded, tuple(feedback)

    before, progressed, succeeded, feedback = asyncio.run(scenario())

    assert before == ()
    assert progressed.progress == 40
    assert succeeded.result == {"forecast": "晴"}
    assert [event.event_type for event in feedback] == [
        "capability.progress",
        "capability.result",
    ]
    assert feedback[0].payload["progress"] == 40
    assert feedback[1].payload["task_status"] == "succeeded"
    assert "处理中" not in str(feedback)


def test_delivery_receipt_is_persisted_before_feedback_and_unknown_is_not_retried(
    tmp_path,
):
    async def scenario():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        outbox = OutboxService(path)
        bundle = DeliveryBundle.create(
            bundle_id="bundle-1",
            correlation_id="corr-delivery-1",
            persona_id="persona-1",
            group_id="group-1",
            topic_id="topic-1",
            parts=(
                DeliveryPart.create(
                    part_id="part-1",
                    kind=DeliveryPartKind.TEXT,
                    payload={"text": "安全文本"},
                    order=0,
                    idempotency_key="delivery-1",
                    expires_at=150,
                ),
            ),
            created_at=100,
            expires_at=160,
        )
        outbox.commit_bundle(bundle)
        claimed = outbox.claim_ready(now=101)[0]
        feedback = []

        async def sink(event):
            feedback.append((event, outbox.outbox("part-1").status.value))

        coordinator = ExecutionCoordinator(path, outbox=outbox, event_sink=sink)
        durable = await coordinator.apply_delivery_receipt(
            DeliveryReceipt.create(
                receipt_id="receipt-unknown-1",
                part_id=claimed.part_id,
                status=DeliveryReceiptStatus.UNKNOWN,
                occurred_at=102,
                error_code="transport_ambiguous",
            )
        )
        retry = outbox.claim_ready(now=103)
        return durable, tuple(feedback), retry

    durable, feedback, retry = asyncio.run(scenario())

    assert durable.status.value == "unknown"
    assert feedback[0][0].event_type == "delivery.unknown"
    assert feedback[0][1] == "unknown"
    assert retry == ()


def test_coordinator_never_automatically_retries_side_effect_nodes(tmp_path):
    async def fail(*args):
        del args
        return NodeExecutionResult.failed("provider_timeout")

    node = replace(_node("capability", "CALL_CAPABILITY"), retry_limit=2)
    plan = _plan(
        nodes=(node,),
        edges=(),
        concurrency=1,
    )
    context = _context(max_concurrency=1)

    async def scenario():
        coordinator = ExecutionCoordinator(
            tmp_path / "groupmate-social-runtime-v2.db",
            executors={"CALL_CAPABILITY": fail},
        )
        coordinator.submit(
            plan,
            ActionPlanValidator().validate(plan, context),
            now=100,
        )
        return await coordinator.advance(plan.plan_id, now=101)

    execution = asyncio.run(scenario())

    assert execution.status == "failed"
    assert execution.runnable_node_ids == ()
    assert execution.node_state("capability").attempts == 1


def test_coordinator_does_not_run_a_node_after_its_deadline(tmp_path):
    calls = []

    async def execute(*args):
        calls.append(args)
        return NodeExecutionResult.succeeded()

    node = replace(_node("text", "GENERATE_TEXT"), deadline_at=105)
    plan = _plan(nodes=(node,), edges=(), concurrency=1)
    context = _context(max_concurrency=1)

    async def scenario():
        coordinator = ExecutionCoordinator(
            tmp_path / "groupmate-social-runtime-v2.db",
            executors={"GENERATE_TEXT": execute},
        )
        coordinator.submit(
            plan,
            ActionPlanValidator().validate(plan, context),
            now=100,
        )
        return await coordinator.advance(plan.plan_id, now=105)

    execution = asyncio.run(scenario())

    assert calls == []
    assert execution.status == "failed"
    assert execution.node_state("text").error_code == "node_deadline_expired"


def test_persisted_requester_confirmation_wakes_confirmation_node(tmp_path):
    async def scenario():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        runtime = TaskRuntime(path)
        descriptor = CapabilityDescriptor.create(
            capability_id="calendar.create",
            provider_id="provider.calendar",
            input_schema=(CapabilityField("title", "string"),),
            output_schema=(CapabilityField("event_id", "string"),),
            risk_level=RiskLevel.EXTERNAL_SIDE_EFFECT,
            required_scopes=("calendar.write",),
            idempotent=True,
            cancellable=True,
            supports_progress=False,
            expected_latency_ms=5000,
            media_output_kinds=(),
            confirmation_policy=ConfirmationPolicy.REQUIRED,
        )
        task = runtime.propose(
            descriptor,
            CapabilityRequest.create(
                requester_id="user-1",
                persona_id="persona-1",
                group_id="group-1",
                topic_id="topic-1",
                input_payload={"title": "开会"},
                authorization_scopes=("calendar.write",),
                idempotency_key="calendar-1",
                correlation_id="corr-calendar-1",
                expires_at=200,
                direct_request=True,
            ),
            now=100,
        )
        task = runtime.start(task.task_id, now=101)

        async def confirmation_executor(*args):
            del args
            current = runtime.load(task.task_id)
            if current.status.value == "awaiting_confirmation":
                return NodeExecutionResult.waiting({"task_id": task.task_id})
            return NodeExecutionResult.succeeded({"task_id": task.task_id})

        node = _node("confirmation", "REQUEST_CONFIRMATION")
        plan = _plan(nodes=(node,), edges=(), concurrency=1)
        context = _context(max_concurrency=1)
        coordinator = ExecutionCoordinator(
            path,
            task_runtime=runtime,
            executors={"REQUEST_CONFIRMATION": confirmation_executor},
        )
        coordinator.submit(
            plan,
            ActionPlanValidator().validate(plan, context),
            now=101,
        )
        waiting = await coordinator.advance(plan.plan_id, now=102)
        await coordinator.confirm_task(
            task.task_id,
            confirmer_id="user-1",
            now=103,
        )
        awakened = coordinator.load(plan.plan_id)
        completed = await coordinator.advance(plan.plan_id, now=104)
        return waiting, awakened, completed

    waiting, awakened, completed = asyncio.run(scenario())

    assert waiting.node_state("confirmation").status is NodeExecutionStatus.WAITING
    assert waiting.runnable_node_ids == ()
    assert awakened.runnable_node_ids == ("confirmation",)
    assert completed.status == "completed"


def test_restart_replays_persisted_task_and_delivery_feedback_to_durable_fabric(
    tmp_path,
):
    async def scenario():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        runtime, task = _task_runtime(path)
        outbox = OutboxService(path)
        bundle = DeliveryBundle.create(
            bundle_id="bundle-recovery",
            correlation_id="corr-delivery-recovery",
            persona_id="persona-1",
            group_id="group-1",
            topic_id="topic-1",
            parts=(
                DeliveryPart.create(
                    part_id="part-recovery",
                    kind=DeliveryPartKind.TEXT,
                    payload={"text": "安全文本"},
                    order=0,
                    idempotency_key="delivery-recovery",
                    expires_at=190,
                ),
            ),
            created_at=100,
            expires_at=200,
        )
        outbox.commit_bundle(bundle)
        outbox.claim_ready(now=101)

        async def crash_after_domain_commit(event):
            del event
            raise RuntimeError("fabric unavailable")

        first = ExecutionCoordinator(
            path,
            task_runtime=runtime,
            outbox=outbox,
            event_sink=crash_after_domain_commit,
        )
        with pytest.raises(RuntimeError, match="fabric unavailable"):
            await first.apply_provider_event(
                ProviderEvent.create(
                    event_id="provider:recovery-result",
                    task_id=task.task_id,
                    kind="succeeded",
                    occurred_at=120,
                    result={"forecast": "晴"},
                )
            )
        with pytest.raises(RuntimeError, match="fabric unavailable"):
            await first.apply_delivery_receipt(
                DeliveryReceipt.create(
                    receipt_id="receipt-recovery",
                    part_id="part-recovery",
                    status=DeliveryReceiptStatus.SUCCESS,
                    occurred_at=121,
                    platform_message_id="qq-recovery",
                )
            )

        store = SQLiteSocialEventStore(path)

        async def durable_sink(event):
            return store.append(event)

        restarted = ExecutionCoordinator(
            path,
            task_runtime=TaskRuntime(path),
            outbox=OutboxService(path),
            event_sink=durable_sink,
        )
        await restarted.recover_feedback()
        await restarted.recover_feedback()
        return store.event_ids()

    event_ids = asyncio.run(scenario())

    assert event_ids.count("provider-feedback:provider:recovery-result") == 1
    assert event_ids.count("delivery-feedback:receipt-recovery") == 1


def test_coordinator_normalizes_direct_provider_event_construction(tmp_path):
    async def scenario():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        runtime, task = _task_runtime(path)
        feedback = []

        async def sink(event):
            feedback.append(event)

        coordinator = ExecutionCoordinator(
            path,
            task_runtime=runtime,
            event_sink=sink,
        )
        completed = await coordinator.apply_provider_event(
            ProviderEvent(
                event_id="provider:direct-construction",
                task_id=task.task_id,
                kind="succeeded",
                occurred_at=120,
                result={"forecast": "晴"},
            )
        )
        return completed, feedback

    completed, feedback = asyncio.run(scenario())

    assert completed.status.value == "succeeded"
    assert feedback[0].event_type == "capability.result"


def test_coordinator_normalizes_direct_node_result_construction(tmp_path):
    async def execute(*args):
        del args
        return NodeExecutionResult(status="succeeded", output={"safe": True})

    node = _node("text", "GENERATE_TEXT")
    plan = _plan(nodes=(node,), edges=(), concurrency=1)
    context = _context(max_concurrency=1)

    async def scenario():
        coordinator = ExecutionCoordinator(
            tmp_path / "groupmate-social-runtime-v2.db",
            executors={"GENERATE_TEXT": execute},
        )
        coordinator.submit(
            plan,
            ActionPlanValidator().validate(plan, context),
            now=100,
        )
        return await coordinator.advance(plan.plan_id, now=101)

    execution = asyncio.run(scenario())

    assert execution.status == "completed"
    assert execution.node_state("text").output == {"safe": True}
