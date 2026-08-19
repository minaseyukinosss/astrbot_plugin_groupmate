from __future__ import annotations

import asyncio

import pytest

from groupmate.social_runtime.contracts import RuntimeMode, SocialEventEnvelope
from groupmate.social_runtime.manager import SocialRuntimeManager
from groupmate.social_runtime.scene_actor import TaskResultDisposition
from groupmate.social_runtime.tasks.contracts import (
    CapabilityDescriptor,
    CapabilityField,
    CapabilityRequest,
    ConfirmationPolicy,
    ProviderEvent,
    RiskLevel,
)
from tests.factories import social_event_values
from groupmate.social_runtime.tasks.runtime import TaskRuntime


def _message(message_id, *, actor_id="user-1", direct=False, occurred_at=100):
    return SocialEventEnvelope.create(
        **social_event_values(
            event_id=f"qq:{message_id}",
            source_message_id=message_id,
            persona_id="persona-1",
            group_id="group-1",
            actor_id=actor_id,
            occurred_at=occurred_at,
            received_at=occurred_at,
            correlation_id=f"corr:{message_id}",
            payload={"text": message_id, "direct_address": direct},
        )
    )


def _descriptor():
    return CapabilityDescriptor.create(
        capability_id="lookup.weather",
        provider_id="provider.weather",
        input_schema=(CapabilityField("city", "string"),),
        output_schema=(CapabilityField("forecast", "string"),),
        risk_level=RiskLevel.READ_ONLY,
        required_scopes=("weather.read",),
        idempotent=True,
        cancellable=False,
        supports_progress=False,
        expected_latency_ms=5000,
        media_output_kinds=(),
        confirmation_policy=ConfirmationPolicy.NEVER,
    )


@pytest.mark.parametrize(
    ("direct_request", "task_topic", "expected"),
    (
        (True, "topic-a", TaskResultDisposition.SEND),
        (False, "topic-a", TaskResultDisposition.DEFER),
        (False, "topic-gone", TaskResultDisposition.SILENCE),
    ),
)
def test_task_finishes_accurately_after_topic_change_and_actor_rejudges_visibility(
    tmp_path, direct_request, task_topic, expected
):
    async def scenario():
        manager = SocialRuntimeManager(
            database_path=tmp_path / "groupmate-social-runtime-v2.db",
            persona_id="persona-1",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("group-1",),
        )
        await manager.start()
        await manager.ingest(
            _message("topic-a", direct=True, occurred_at=100)
        )
        actor = await manager.fabric.notify("persona-1", "group-1")
        await manager.fabric.drain()

        task = manager.task_runtime.propose(
            _descriptor(),
            CapabilityRequest.create(
                requester_id="user-1",
                persona_id="persona-1",
                group_id="group-1",
                topic_id=task_topic,
                input_payload={"city": "上海"},
                authorization_scopes=("weather.read",),
                idempotency_key=f"weather:{direct_request}:{task_topic}",
                correlation_id=f"corr:task:{direct_request}:{task_topic}",
                expires_at=200,
                direct_request=direct_request,
            ),
            now=101,
        )
        task = manager.task_runtime.start(task.task_id, now=102)
        task = manager.task_runtime.start(task.task_id, now=103)

        await manager.ingest(
            _message("topic-b", actor_id="user-2", occurred_at=110)
        )
        await manager.fabric.drain()
        completed = await manager.coordinator.apply_provider_event(
            ProviderEvent.create(
                event_id=f"provider:done:{direct_request}:{task_topic}",
                task_id=task.task_id,
                kind="succeeded",
                occurred_at=120,
                result={"forecast": "晴"},
            )
        )
        requests = await manager.fabric.drain()
        same_actor = await manager.fabric.notify("persona-1", "group-1")
        await manager.close()
        return actor, same_actor, completed, requests

    actor, same_actor, completed, requests = asyncio.run(scenario())

    assert same_actor is actor
    assert completed.status.value == "succeeded"
    assert completed.result == {"forecast": "晴"}
    assert len(requests) == 1
    request = requests[0]
    assert request.event.event_type == "capability.result"
    assert request.event.payload["task_status"] == "succeeded"
    assert request.event.payload["result"] == {"forecast": "晴"}
    assert request.task_result_decision is not None
    assert request.task_result_decision.disposition is expected
    if direct_request:
        assert "direct_request_obligation" in request.task_result_decision.reason_codes


def test_execution_feedback_cannot_escape_manager_group_allowlist(tmp_path):
    async def scenario():
        manager = SocialRuntimeManager(
            database_path=tmp_path / "groupmate-social-runtime-v2.db",
            persona_id="persona-1",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("group-1",),
        )
        await manager.start()
        task = manager.task_runtime.propose(
            _descriptor(),
            CapabilityRequest.create(
                requester_id="user-1",
                persona_id="persona-1",
                group_id="group-outside-allowlist",
                topic_id="topic-a",
                input_payload={"city": "上海"},
                authorization_scopes=("weather.read",),
                idempotency_key="outside-group",
                correlation_id="corr:outside-group",
                expires_at=200,
                direct_request=True,
            ),
            now=100,
        )
        task = manager.task_runtime.start(task.task_id, now=101)
        task = manager.task_runtime.start(task.task_id, now=102)
        with pytest.raises(ValueError, match="enabled group"):
            await manager.coordinator.apply_provider_event(
                ProviderEvent.create(
                    event_id="provider:outside-group",
                    task_id=task.task_id,
                    kind="succeeded",
                    occurred_at=110,
                    result={"forecast": "晴"},
                )
            )
        actors = manager.fabric.actors
        await manager.close()
        return actors

    assert asyncio.run(scenario()) == ()


def test_manager_restart_recovers_persisted_provider_feedback_without_deadlock(
    tmp_path,
):
    async def scenario():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        runtime = TaskRuntime(path)
        task = runtime.propose(
            _descriptor(),
            CapabilityRequest.create(
                requester_id="user-1",
                persona_id="persona-1",
                group_id="group-1",
                topic_id="topic-a",
                input_payload={"city": "上海"},
                authorization_scopes=("weather.read",),
                idempotency_key="restart-feedback",
                correlation_id="corr:restart-feedback",
                expires_at=200,
                direct_request=True,
            ),
            now=100,
        )
        task = runtime.start(task.task_id, now=101)
        task = runtime.start(task.task_id, now=102)
        runtime.apply_event(
            ProviderEvent.create(
                event_id="provider:restart-feedback",
                task_id=task.task_id,
                kind="succeeded",
                occurred_at=110,
                result={"forecast": "晴"},
            )
        )
        manager = SocialRuntimeManager(
            database_path=path,
            persona_id="persona-1",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("group-1",),
        )
        await asyncio.wait_for(manager.start(), timeout=0.5)
        evaluations = await manager.drain()
        event_ids = manager.event_store.event_ids()
        await manager.close()
        return evaluations, event_ids

    evaluations, event_ids = asyncio.run(scenario())

    assert len(evaluations) == 1
    assert "provider-feedback:provider:restart-feedback" in event_ids
