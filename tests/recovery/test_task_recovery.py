from __future__ import annotations

from groupmate.adapters.astrbot_capabilities import AstrBotCapabilityAdapter
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
from groupmate.social_runtime.tasks.runtime import TaskRuntime


def _descriptor(**overrides) -> CapabilityDescriptor:
    values = {
        "capability_id": "report.generate",
        "provider_id": "astrbot.report",
        "input_schema": (CapabilityField("period", "string"),),
        "output_schema": (CapabilityField("report_id", "string"),),
        "risk_level": RiskLevel.LOW_IMPACT,
        "required_scopes": ("report:create",),
        "idempotent": True,
        "cancellable": True,
        "supports_progress": True,
        "expected_latency_ms": 5_000,
        "media_output_kinds": (),
        "confirmation_policy": ConfirmationPolicy.NEVER,
    }
    values.update(overrides)
    return CapabilityDescriptor.create(**values)


def _request() -> CapabilityRequest:
    return CapabilityRequest.create(
        requester_id="user-1",
        persona_id="persona-1",
        group_id="group-1",
        topic_id="topic-report",
        input_payload={"period": "本周"},
        authorization_scopes=("report:create",),
        idempotency_key="report-request-1",
        correlation_id="corr-1",
        expires_at=500,
    )


class _QueryableProvider:
    def __init__(self, descriptor: CapabilityDescriptor) -> None:
        self.descriptor = descriptor
        self.start_calls = 0
        self.query_calls = 0

    def start(self, task):
        self.start_calls += 1
        raise AssertionError("recovery must not redo provider side effects")

    def query_status(self, task):
        self.query_calls += 1
        return ProviderEvent.create(
            event_id="recovered-success",
            task_id=task.task_id,
            kind=ProviderEventKind.SUCCEEDED,
            occurred_at=110,
            result={"report_id": "report-8"},
        )

    def cancel(self, task):
        raise AssertionError("not used")


class _UnqueryableProvider:
    def __init__(self, descriptor: CapabilityDescriptor) -> None:
        self.descriptor = descriptor
        self.start_calls = 0

    def start(self, task):
        self.start_calls += 1
        raise AssertionError("recovery must not redo provider side effects")

    def cancel(self, task):
        raise AssertionError("not used")


def _persist_running(path, descriptor):
    runtime = TaskRuntime(path)
    proposed = runtime.propose(descriptor, _request(), now=100)
    queued = runtime.start(proposed.task_id, now=101)
    assert queued.status == TaskStatus.QUEUED
    running = runtime.start(proposed.task_id, now=102)
    assert running.status == TaskStatus.RUNNING
    return running


def test_recovery_queries_idempotent_provider_without_restarting_side_effect(tmp_path):
    path = tmp_path / "social-runtime.db"
    descriptor = _descriptor()
    running = _persist_running(path, descriptor)
    provider = _QueryableProvider(descriptor)
    adapter = AstrBotCapabilityAdapter((provider,))

    recovered = TaskRuntime(path).recover(adapter, now=120)

    assert recovered[0].task_id == running.task_id
    assert recovered[0].status == TaskStatus.SUCCEEDED
    assert provider.query_calls == 1
    assert provider.start_calls == 0


def test_unqueryable_running_task_becomes_unknown_for_governance(tmp_path):
    path = tmp_path / "social-runtime.db"
    descriptor = _descriptor()
    running = _persist_running(path, descriptor)
    provider = _UnqueryableProvider(descriptor)
    adapter = AstrBotCapabilityAdapter((provider,))

    recovered = TaskRuntime(path).recover(adapter, now=120)

    assert recovered[0].task_id == running.task_id
    assert recovered[0].status == TaskStatus.UNKNOWN
    assert recovered[0].error_code == "provider_status_unqueryable"
    assert provider.start_calls == 0


def test_non_idempotent_running_task_is_not_queried_or_restarted(tmp_path):
    path = tmp_path / "social-runtime.db"
    descriptor = _descriptor(idempotent=False)
    _persist_running(path, descriptor)
    provider = _QueryableProvider(descriptor)
    adapter = AstrBotCapabilityAdapter((provider,))

    recovered = TaskRuntime(path).recover(adapter, now=120)

    assert recovered[0].status == TaskStatus.UNKNOWN
    assert recovered[0].error_code == "provider_status_not_safe_to_query"
    assert provider.query_calls == 0
    assert provider.start_calls == 0
