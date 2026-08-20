from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.adapters.onebot_delivery import OneBotDeliveryAdapter
from groupmate.settings import SocialRuntimeSettings
from groupmate.social_runtime.actions.contracts import (
    DeliveryBundle,
    DeliveryPart,
    DeliveryPartKind,
    OutboxStatus,
    PlanContext,
)
from groupmate.social_runtime.actions.coordinator import (
    NodeExecutionResult,
    PlanExecutionStatus,
)
from groupmate.social_runtime.actions.generation import (
    GenerationRequest,
    SafeTextGeneration,
)
from groupmate.social_runtime.actions.style import (
    PersonaStyleSnapshot,
    StyleContext,
    StyleDirector,
)
from groupmate.social_runtime.cognition.contracts import CognitiveObservation
from groupmate.social_runtime.contracts import RuntimeMode, SocialEventEnvelope
from groupmate.social_runtime.delivery.dispatcher import DeliveryDispatcher
from groupmate.social_runtime.delivery.outbox import (
    OutboxAuthorizationError,
    OutboxService,
)
from groupmate.social_runtime.governor import GovernorResult
from groupmate.social_runtime.manager import RuntimeModeUnavailable, SocialRuntimeManager
from groupmate.social_runtime.persona.modes import PersonaModeState
from groupmate.social_runtime.planner import ActionPlanner
from groupmate.social_runtime.tasks.contracts import (
    CapabilityDescriptor,
    CapabilityField,
    CapabilityRequest,
    ConfirmationPolicy,
    ProviderEvent,
    ProviderEventKind,
    RiskLevel,
    TaskStatus,
)
from groupmate.social_runtime.tasks.runtime import InvalidTaskTransition, TaskRuntime
from groupmate.social_runtime.validator import ActionPlanValidator
from tests.factories import social_event_values


FAKE_GROUP = "test-group-1001"
SHADOW_GROUP = "shadow-group-2002"


class _HelpWorker:
    name = "direct_interaction"

    async def observe(self, frame, context):
        return (
            CognitiveObservation.create(
                worker=self.name,
                kind="help_request",
                proposition={
                    "subject_id": frame.candidate_audiences[0],
                    "topic_id": frame.focus_topic_ids[0],
                },
                confidence=1.0,
                evidence_event_ids=(frame.focus_event_ids[0],),
                scene_version=frame.scene_version,
                expires_at=context.now + 20,
                uncertainty=(),
            ),
        )


def _message(message_id="source-1", *, group_id=FAKE_GROUP):
    return SocialEventEnvelope.create(
        **social_event_values(
            event_id=f"qq:{message_id}",
            source_message_id=message_id,
            persona_id="persona-1",
            group_id=group_id,
            actor_id="user-1",
            occurred_at=100,
            received_at=100,
            correlation_id=f"corr:{message_id}",
            payload={"text": "帮我确认一下", "direct_address": True},
        )
    )


def _plan_context(frame):
    return PlanContext(
        now=100,
        group_id=frame.group_id,
        persona_id="persona-1",
        scene_version=frame.scene_version,
        config_version=frame.config_version,
        persona_version=frame.persona_state_version,
        constitution_version=1,
        relationship_version=0,
        state_version=frame.persona_state_version,
        requester_permissions=("generate_text", "send_message"),
        supported_node_kinds=("GENERATE_TEXT", "SEND_BUNDLE"),
        allowed_audience_ids=frame.candidate_audiences,
        allowed_owner_ids=("text_generator", "bundle_delivery"),
        max_nodes=24,
        max_plan_duration=30,
        max_retries=2,
        max_autonomous_followups=1,
        constitution_allowed=True,
        relationship_allowed=True,
        state_allowed=True,
        max_risk_score=0,
        allowed_media_references=(),
        max_budget_cost=0,
        max_concurrency=1,
        confirmed_ids=(),
    )


def _style_directive():
    return StyleDirector().direct(
        StyleContext(
            persona=PersonaStyleSnapshot("persona-1", None, ("concise",)),
            mode=PersonaModeState.social(),
            relationship=None,
            culture_patterns=(),
            recent_outputs=(),
            act="direct_answer",
            token_budget=26,
        )
    )


def _authorized_plan(group_id):
    context = PlanContext(
        now=100,
        group_id=group_id,
        persona_id="persona-1",
        scene_version=1,
        config_version=1,
        persona_version=1,
        constitution_version=1,
        relationship_version=0,
        state_version=1,
        requester_permissions=("generate_text", "send_message"),
        supported_node_kinds=("GENERATE_TEXT", "SEND_BUNDLE"),
        allowed_audience_ids=("user-1",),
        allowed_owner_ids=("text_generator", "bundle_delivery"),
        max_nodes=24,
        max_plan_duration=30,
        max_retries=2,
        max_autonomous_followups=1,
        constitution_allowed=True,
        relationship_allowed=True,
        state_allowed=True,
        max_risk_score=0,
        allowed_media_references=(),
        max_budget_cost=0,
        max_concurrency=1,
        confirmed_ids=(),
    )
    result = GovernorResult(
        "ACT",
        ("intention-gate",),
        (),
        ("selected_by_social_utility",),
        None,
        ("hard_gate_v1",),
    )
    plan = ActionPlanner().plan(
        {
            "intention_id": "intention-gate",
            "target_id": "user-1",
            "topic_id": "topic-1",
        },
        context,
        result,
    )
    return plan, ActionPlanValidator().validate(plan, context)


def _task_descriptor(**overrides):
    values = {
        "capability_id": "lookup.report",
        "provider_id": "provider.report",
        "input_schema": (CapabilityField("query", "string"),),
        "output_schema": (CapabilityField("answer", "string"),),
        "risk_level": RiskLevel.READ_ONLY,
        "required_scopes": ("report.read",),
        "idempotent": True,
        "cancellable": True,
        "supports_progress": True,
        "expected_latency_ms": 5_000,
        "media_output_kinds": (),
        "confirmation_policy": ConfirmationPolicy.NEVER,
    }
    values.update(overrides)
    return CapabilityDescriptor.create(**values)


def _running_task(runtime, *, suffix="1", expires_at=150):
    task = runtime.propose(
        _task_descriptor(),
        CapabilityRequest.create(
            requester_id="user-1",
            persona_id="persona-1",
            group_id=FAKE_GROUP,
            topic_id="topic-1",
            input_payload={"query": "status"},
            authorization_scopes=("report.read",),
            idempotency_key=f"task-{suffix}",
            correlation_id=f"corr:task-{suffix}",
            expires_at=expires_at,
            direct_request=False,
        ),
        now=100,
    )
    runtime.start(task.task_id, now=101)
    return runtime.start(task.task_id, now=102)


def _delivery_bundle(*, bundle_id="bundle-fault", parts=3, group_id=FAKE_GROUP):
    delivery_parts = tuple(
        DeliveryPart.create(
            part_id=f"{bundle_id}:part:{index}",
            kind=DeliveryPartKind.TEXT,
            payload={"text": f"part-{index}"},
            order=index,
            idempotency_key=f"{bundle_id}:key:{index}",
            expires_at=300,
        )
        for index in range(parts)
    )
    return DeliveryBundle.create(
        bundle_id=bundle_id,
        correlation_id="corr:fault",
        persona_id="persona-1",
        group_id=group_id,
        topic_id="topic-1",
        parts=delivery_parts,
        created_at=100,
        expires_at=300,
    )


def test_gate_c_only_promotes_explicit_fake_groups_and_keeps_other_groups_safe(
    tmp_path,
):
    schema = json.loads(
        (Path(__file__).parents[2] / "_conf_schema.json").read_text(encoding="utf-8")
    )
    assert "runtime_mode" not in schema

    settings = SocialRuntimeSettings.from_mapping(
        {
            "runtime_mode": "SOCIAL_RUNTIME",
            "enabled_groups": [FAKE_GROUP, SHADOW_GROUP],
            "social_runtime_test_groups": [FAKE_GROUP],
            "generation_provider": "provider:test",
            "persona_id": "persona-1",
        }
    )
    assert settings.social_runtime_test_groups == (FAKE_GROUP,)

    async def scenario():
        bridge = AstrBotSocialRuntimeBridge(object(), settings, tmp_path)
        await bridge.start()
        modes = (
            bridge.manager.group_mode(FAKE_GROUP),
            bridge.manager.group_mode(SHADOW_GROUP),
            bridge.manager.group_mode("outside"),
        )
        with pytest.raises(RuntimeModeUnavailable, match="Gate C"):
            bridge.manager.require_social_runtime_group(SHADOW_GROUP)
        await bridge.close()
        return modes

    assert asyncio.run(scenario()) == (
        RuntimeMode.SOCIAL_RUNTIME,
        RuntimeMode.SHADOW,
        RuntimeMode.OFF,
    )


def test_gate_c_rejects_missing_or_out_of_scope_test_allowlist_before_io(tmp_path):
    for social_groups in ((), ("outside",)):
        path = tmp_path / f"{len(social_groups)}.db"
        with pytest.raises(RuntimeModeUnavailable, match="test group allowlist"):
            SocialRuntimeManager(
                database_path=path,
                persona_id="persona-1",
                mode=RuntimeMode.SOCIAL_RUNTIME,
                enabled_groups=(FAKE_GROUP,),
                social_runtime_test_groups=social_groups,
            )
        assert not path.exists()


def test_gate_c_cannot_be_bypassed_through_manager_coordinator_or_outbox(tmp_path):
    manager = SocialRuntimeManager(
        database_path=tmp_path / "gate-authority.db",
        persona_id="persona-1",
        mode=RuntimeMode.SOCIAL_RUNTIME,
        enabled_groups=(FAKE_GROUP, SHADOW_GROUP),
        social_runtime_test_groups=(FAKE_GROUP,),
    )
    shadow_plan, validation = _authorized_plan(SHADOW_GROUP)

    with pytest.raises(RuntimeModeUnavailable, match="Gate C"):
        manager.coordinator.submit(shadow_plan, validation, now=100)
    with pytest.raises(OutboxAuthorizationError, match="Gate C"):
        manager.outbox.commit_bundle(
            _delivery_bundle(bundle_id="shadow-bundle", group_id=SHADOW_GROUP)
        )
    with pytest.raises(OutboxAuthorizationError, match="validated ActionPlan"):
        manager.outbox.commit_bundle(
            _delivery_bundle(bundle_id="orphan-bundle", group_id=FAKE_GROUP)
        )

    assert manager.outbox.count() == 0


def test_event_to_receipt_lineage_survives_required_generator_failure(tmp_path):
    async def scenario():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        manager = SocialRuntimeManager(
            database_path=path,
            persona_id="persona-1",
            mode=RuntimeMode.SOCIAL_RUNTIME,
            enabled_groups=(FAKE_GROUP, SHADOW_GROUP),
            social_runtime_test_groups=(FAKE_GROUP,),
            cognition_workers={"direct_interaction": _HelpWorker()},
        )
        await manager.start()
        await manager.ingest(_message())
        evaluation = (await manager.drain(now=100))[0]
        selected_id = evaluation.governor_result.selected_intention_ids[0]
        intention = {
            "intention_id": selected_id,
            "target_id": evaluation.frame.candidate_audiences[0],
            "topic_id": evaluation.frame.focus_topic_ids[0],
        }
        context = _plan_context(evaluation.frame)
        plan = ActionPlanner().plan(
            intention,
            context,
            evaluation.governor_result,
        )
        validation = ActionPlanValidator().validate(plan, context)
        assert validation.accepted

        async def generate(plan, node, dependencies, now):
            del plan, node, dependencies, now
            result = SafeTextGeneration().generate(
                GenerationRequest(
                    directive=_style_directive(),
                    required=True,
                    recent_outputs=(),
                    allowed_media_references=(),
                    verified_capability_results=(),
                ),
                generator=lambda directive: (_ for _ in ()).throw(
                    RuntimeError("generator offline")
                ),
                repairer=lambda draft, directive, violations: draft,
            )
            return NodeExecutionResult.succeeded(
                {
                    "text": result.draft.text,
                    "generation_outcome": result.outcome,
                }
            )

        async def bundle(plan, node, dependencies, now):
            del node
            text = str(dependencies["generate_text"]["text"])
            part = DeliveryPart.create(
                part_id=f"{plan.plan_id}:part:0",
                kind=DeliveryPartKind.TEXT,
                payload={"text": text},
                order=0,
                idempotency_key=f"{plan.plan_id}:send:0",
                expires_at=plan.expires_at,
            )
            delivery = DeliveryBundle.create(
                bundle_id=f"{plan.plan_id}:bundle",
                correlation_id=plan.correlation_id,
                persona_id=plan.persona_id,
                group_id=plan.group_id,
                topic_id=plan.topic_id,
                parts=(part,),
                created_at=now,
                expires_at=plan.expires_at,
            )
            manager.outbox.commit_bundle(delivery)
            return NodeExecutionResult.succeeded(
                {"bundle_id": delivery.bundle_id, "part_id": part.part_id}
            )

        manager.coordinator.executors.update(
            {"GENERATE_TEXT": generate, "SEND_BUNDLE": bundle}
        )
        submitted = manager.submit_plan(plan, validation, now=100)
        first = await manager.coordinator.advance(submitted.plan.plan_id, now=101)
        completed = await manager.coordinator.advance(first.plan.plan_id, now=102)

        platform_calls = []

        async def fake_onebot(*, group_id, segments, idempotency_key):
            platform_calls.append((group_id, segments, idempotency_key))
            return {"message_id": "fake-onebot-message-1"}

        dispatcher = DeliveryDispatcher(
            manager.outbox,
            OneBotDeliveryAdapter(fake_onebot, clock=lambda: 103),
            receipt_handler=manager.coordinator.apply_delivery_receipt,
        )
        sent = await dispatcher.dispatch_next(now=103)
        await manager.fabric.drain()
        durable_plan = manager.coordinator.load(plan.plan_id)
        durable_part = manager.outbox.outbox(sent.part_id)
        ledger = manager.outbox.bot_ledger(sent.part_id)
        decisions = manager.event_store.shadow_evaluations("persona-1", FAKE_GROUP)
        event_ids = manager.event_store.event_ids()
        await manager.close()
        return (
            evaluation,
            durable_plan,
            durable_part,
            ledger,
            decisions,
            event_ids,
            platform_calls,
        )

    (
        evaluation,
        durable_plan,
        durable_part,
        ledger,
        decisions,
        event_ids,
        platform_calls,
    ) = asyncio.run(scenario())

    assert evaluation.governor_result.outcome == "ACT"
    assert durable_plan.status is PlanExecutionStatus.COMPLETED
    assert durable_plan.node_state("generate_text").output == {
        "generation_outcome": "fallback",
        "text": "暂时无法可靠回答。",
    }
    assert durable_part.status is OutboxStatus.SENT
    assert durable_part.idempotency_key == f"{durable_plan.plan.plan_id}:send:0"
    assert ledger.bundle_id == f"{durable_plan.plan.plan_id}:bundle"
    assert ledger.correlation_id == durable_plan.plan.correlation_id
    assert durable_plan.plan.intention_ids[0] in decisions[0]["governor_result"][
        "selected_intention_ids"
    ]
    assert any(event_id.startswith("delivery-feedback:") for event_id in event_ids)
    assert platform_calls == [
        (
            FAKE_GROUP,
            [{"type": "text", "data": {"text": "暂时无法可靠回答。"}}],
            f"{durable_plan.plan.plan_id}:send:0",
        )
    ]


def test_provider_timeout_expires_without_querying_or_restarting_provider(tmp_path):
    runtime = TaskRuntime(tmp_path / "timeout.db")
    running = _running_task(runtime, expires_at=105)

    class _Adapter:
        def can_query(self, task):
            raise AssertionError("expired task must not query provider")

    recovered = runtime.recover(_Adapter(), now=105)

    assert recovered == (runtime.load(running.task_id),)
    assert recovered[0].status is TaskStatus.EXPIRED


def test_duplicate_provider_progress_is_idempotent(tmp_path):
    runtime = TaskRuntime(tmp_path / "duplicate-progress.db")
    running = _running_task(runtime)
    progress = ProviderEvent.create(
        event_id="provider:progress:1",
        task_id=running.task_id,
        kind=ProviderEventKind.PROGRESS,
        occurred_at=103,
        progress=40,
    )

    first = runtime.apply_event(progress)
    second = runtime.apply_event(progress)

    assert second == first
    assert second.progress == 40
    assert runtime.event_count(running.task_id) == 4


def test_cancel_and_success_race_preserves_first_terminal_provider_fact(tmp_path):
    runtime = TaskRuntime(tmp_path / "cancel-race.db")
    cancel_first = _running_task(runtime, suffix="cancel-first")
    success_first = _running_task(runtime, suffix="success-first")
    canceled = ProviderEvent.create(
        event_id="provider:cancel-first",
        task_id=cancel_first.task_id,
        kind=ProviderEventKind.CANCELED,
        occurred_at=103,
    )
    cancel_late_success = ProviderEvent.create(
        event_id="provider:late-success",
        task_id=cancel_first.task_id,
        kind=ProviderEventKind.SUCCEEDED,
        occurred_at=104,
        result={"answer": "late"},
    )
    succeeded = ProviderEvent.create(
        event_id="provider:success-first",
        task_id=success_first.task_id,
        kind=ProviderEventKind.SUCCEEDED,
        occurred_at=103,
        result={"answer": "done"},
    )
    success_late_cancel = ProviderEvent.create(
        event_id="provider:late-cancel",
        task_id=success_first.task_id,
        kind=ProviderEventKind.CANCELED,
        occurred_at=104,
    )

    assert runtime.apply_event(canceled).status is TaskStatus.CANCELED
    with pytest.raises(InvalidTaskTransition):
        runtime.apply_event(cancel_late_success)
    assert runtime.apply_event(succeeded).status is TaskStatus.SUCCEEDED
    with pytest.raises(InvalidTaskTransition):
        runtime.apply_event(success_late_cancel)


def test_partial_send_unknown_receipt_and_restart_never_blindly_replay(tmp_path):
    path = tmp_path / "partial-send.db"
    outbox = OutboxService(path)
    bundle = _delivery_bundle()
    outbox.commit_bundle(bundle)
    calls = []

    async def fake_onebot(*, group_id, segments, idempotency_key):
        calls.append(idempotency_key)
        if len(calls) == 2:
            raise RuntimeError("connection lost after platform call")
        return {"message_id": f"fake-{len(calls)}"}

    async def scenario():
        dispatcher = DeliveryDispatcher(
            outbox,
            OneBotDeliveryAdapter(fake_onebot, clock=lambda: 110),
        )
        first = await dispatcher.dispatch_next(now=101)
        second = await dispatcher.dispatch_next(now=102)
        return first, second

    first, second = asyncio.run(scenario())
    restarted = OutboxService(path)
    recovered = restarted.recover_inflight(now=120)

    assert first.status is OutboxStatus.SENT
    assert second.status is OutboxStatus.UNKNOWN
    assert restarted.bot_ledger(bundle.parts[0].part_id).platform_message_id == "fake-1"
    assert restarted.outbox(bundle.parts[1].part_id).status is OutboxStatus.UNKNOWN
    assert restarted.outbox(bundle.parts[2].part_id).status is OutboxStatus.READY
    assert recovered == ()
    assert restarted.claim_ready(now=121) == ()
    assert calls == [bundle.parts[0].idempotency_key, bundle.parts[1].idempotency_key]


def test_expired_provider_result_completes_accurately_but_loses_delivery_value(
    tmp_path,
):
    runtime = TaskRuntime(tmp_path / "expired-result.db")
    running = _running_task(runtime, expires_at=105)

    completed = runtime.apply_event(
        ProviderEvent.create(
            event_id="provider:expired-success",
            task_id=running.task_id,
            kind=ProviderEventKind.SUCCEEDED,
            occurred_at=110,
            result={"answer": "accurate but late"},
        )
    )

    assert completed.status is TaskStatus.SUCCEEDED
    assert completed.result == {"answer": "accurate but late"}
    assert completed.delivery_relevant is False


def test_projection_snapshot_failure_does_not_rollback_actor_task_or_shadow_safety(
    tmp_path,
):
    async def scenario():
        manager = SocialRuntimeManager(
            database_path=tmp_path / "projection.db",
            persona_id="persona-1",
            mode=RuntimeMode.SHADOW,
            enabled_groups=(SHADOW_GROUP,),
        )
        await manager.start()
        actor = await manager.fabric.notify("persona-1", SHADOW_GROUP)
        actor.SNAPSHOT_INTERVAL = 1

        def fail_projection_snapshot():
            raise RuntimeError("projection unavailable")

        actor._save_snapshot = fail_projection_snapshot
        task = manager.task_runtime.propose(
            _task_descriptor(),
            CapabilityRequest.create(
                requester_id="user-1",
                persona_id="persona-1",
                group_id=SHADOW_GROUP,
                topic_id="topic-1",
                input_payload={"query": "status"},
                authorization_scopes=("report.read",),
                idempotency_key="projection-task",
                correlation_id="corr:projection-task",
                expires_at=200,
            ),
            now=100,
        )
        await manager.ingest(_message(group_id=SHADOW_GROUP))
        await manager.fabric.drain()
        world = await actor.snapshot()
        result = (
            world,
            actor.snapshot_failure_count,
            manager.task_runtime.load(task.task_id),
            manager.outbox.count(),
            manager.execution_port.calls,
        )
        await manager.close()
        return result

    world, failures, task, outbox_count, execution_calls = asyncio.run(scenario())

    assert world.scene_version == 1
    assert failures == 1
    assert task.status is TaskStatus.PROPOSED
    assert outbox_count == 0
    assert execution_calls == ()
