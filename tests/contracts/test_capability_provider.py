from __future__ import annotations

import pytest

from groupmate.adapters.astrbot_capabilities import (
    AstrBotCapabilityAdapter,
    InvalidProviderEvent,
    ProviderNotRegistered,
)
from groupmate.social_runtime.tasks.contracts import (
    CapabilityDescriptor,
    CapabilityField,
    CapabilityRequest,
    ConfirmationPolicy,
    ProviderEvent,
    ProviderEventKind,
    ProviderMedia,
    RiskLevel,
    TaskRun,
    TaskStatus,
)


def _descriptor(**overrides) -> CapabilityDescriptor:
    values = {
        "capability_id": "weather.lookup",
        "provider_id": "astrbot.weather",
        "input_schema": (CapabilityField("city", "string"),),
        "output_schema": (CapabilityField("summary", "string"),),
        "risk_level": RiskLevel.READ_ONLY,
        "required_scopes": ("weather:read",),
        "idempotent": True,
        "cancellable": False,
        "supports_progress": False,
        "expected_latency_ms": 800,
        "media_output_kinds": ("image",),
        "confirmation_policy": ConfirmationPolicy.NEVER,
    }
    values.update(overrides)
    return CapabilityDescriptor.create(**values)


def _request(**overrides) -> CapabilityRequest:
    values = {
        "requester_id": "user-1",
        "persona_id": "persona-1",
        "group_id": "group-1",
        "topic_id": "topic-weather",
        "input_payload": {"city": "上海"},
        "authorization_scopes": ("weather:read",),
        "idempotency_key": "request-1",
        "correlation_id": "corr-1",
        "expires_at": 200,
    }
    values.update(overrides)
    return CapabilityRequest.create(**values)


def _run(descriptor: CapabilityDescriptor | None = None) -> TaskRun:
    return TaskRun.proposed(
        task_id="task-1",
        descriptor=descriptor or _descriptor(),
        request=_request(),
        now=100,
    ).transition(TaskStatus.RUNNING, now=101)


class _Provider:
    def __init__(self, descriptor: CapabilityDescriptor, event: object) -> None:
        self.descriptor = descriptor
        self.event = event
        self.start_calls = 0
        self.query_calls = 0

    def start(self, task: TaskRun) -> object:
        self.start_calls += 1
        return self.event

    def query_status(self, task: TaskRun) -> object:
        self.query_calls += 1
        return self.event

    def cancel(self, task: TaskRun) -> object:
        return ProviderEvent.create(
            event_id="provider-canceled",
            task_id=task.task_id,
            kind=ProviderEventKind.CANCELED,
            occurred_at=103,
        )


def test_high_risk_descriptor_cannot_disable_confirmation():
    with pytest.raises(ValueError, match="confirmation"):
        _descriptor(
            risk_level=RiskLevel.EXTERNAL_SIDE_EFFECT,
            confirmation_policy=ConfirmationPolicy.NEVER,
        )


def test_adapter_calls_only_a_registered_provider_contract():
    adapter = AstrBotCapabilityAdapter()

    with pytest.raises(ProviderNotRegistered, match="astrbot.weather"):
        adapter.start(_run())


def test_autonomous_catalog_contains_only_installed_allowlisted_low_risk_contracts():
    weather = _descriptor()
    admin = _descriptor(
        capability_id="group.mute",
        provider_id="astrbot.admin",
        risk_level=RiskLevel.EXTERNAL_SIDE_EFFECT,
        confirmation_policy=ConfirmationPolicy.REQUIRED,
    )
    event = ProviderEvent.create(
        event_id="provider-succeeded",
        task_id="task-1",
        kind=ProviderEventKind.SUCCEEDED,
        occurred_at=102,
        result={"summary": "晴"},
    )
    adapter = AstrBotCapabilityAdapter(
        (_Provider(weather, event), _Provider(admin, event)),
        autonomous_allowlist=(("astrbot.weather", "weather.lookup"),),
    )

    assert adapter.autonomous_catalog() == (weather,)
    assert adapter.registered_catalog() == (admin, weather)


def test_reference_feature_names_do_not_create_runtime_capabilities():
    adapter = AstrBotCapabilityAdapter(
        autonomous_allowlist=(("reference.waves", "xw"),)
    )

    assert adapter.registered_catalog() == ()
    assert adapter.autonomous_catalog() == ()


def test_adapter_accepts_validated_structured_result_and_media():
    descriptor = _descriptor()
    event = ProviderEvent.create(
        event_id="provider-succeeded",
        task_id="task-1",
        kind=ProviderEventKind.SUCCEEDED,
        occurred_at=102,
        result={"summary": "晴，25°C"},
        media=(
            ProviderMedia(
                media_id="forecast-card",
                kind="image",
                uri="provider://astrbot.weather/forecast-card",
                mime_type="image/png",
                size_bytes=512,
                sha256="a" * 64,
            ),
        ),
    )
    provider = _Provider(descriptor, event)
    adapter = AstrBotCapabilityAdapter((provider,))

    assert adapter.start(_run(descriptor)) == event
    assert provider.start_calls == 1


@pytest.mark.parametrize(
    "event",
    (
        "任务完成：晴天",
        {"status": "success", "text": "任务完成：晴天"},
    ),
)
def test_adapter_never_parses_bot_text_or_untyped_dicts(event):
    descriptor = _descriptor()
    adapter = AstrBotCapabilityAdapter((_Provider(descriptor, event),))

    with pytest.raises(InvalidProviderEvent, match="ProviderEvent"):
        adapter.start(_run(descriptor))


def test_adapter_rejects_result_that_breaks_declared_output_schema():
    descriptor = _descriptor()
    event = ProviderEvent.create(
        event_id="provider-invalid-result",
        task_id="task-1",
        kind=ProviderEventKind.SUCCEEDED,
        occurred_at=102,
        result={"summary": 25},
    )
    adapter = AstrBotCapabilityAdapter((_Provider(descriptor, event),))

    with pytest.raises(InvalidProviderEvent, match="summary"):
        adapter.start(_run(descriptor))


def test_adapter_rejects_undeclared_media_kind():
    descriptor = _descriptor(media_output_kinds=())
    event = ProviderEvent.create(
        event_id="provider-invalid-media",
        task_id="task-1",
        kind=ProviderEventKind.SUCCEEDED,
        occurred_at=102,
        result={"summary": "晴"},
        media=(
            ProviderMedia(
                media_id="forecast-card",
                kind="image",
                uri="provider://astrbot.weather/forecast-card",
                mime_type="image/png",
                size_bytes=512,
                sha256="b" * 64,
            ),
        ),
    )
    adapter = AstrBotCapabilityAdapter((_Provider(descriptor, event),))

    with pytest.raises(InvalidProviderEvent, match="media kind"):
        adapter.start(_run(descriptor))


def test_adapter_rechecks_authorization_instead_of_trusting_a_forged_task():
    descriptor = _descriptor()
    event = ProviderEvent.create(
        event_id="provider-succeeded",
        task_id="task-1",
        kind=ProviderEventKind.SUCCEEDED,
        occurred_at=102,
        result={"summary": "晴"},
    )
    provider = _Provider(descriptor, event)
    forged = TaskRun.proposed(
        task_id="task-1",
        descriptor=descriptor,
        request=_request(authorization_scopes=()),
        now=100,
    ).transition(TaskStatus.RUNNING, now=101)
    adapter = AstrBotCapabilityAdapter((provider,))

    with pytest.raises(InvalidProviderEvent, match="authorization"):
        adapter.start(forged)
    assert provider.start_calls == 0


def test_adapter_rechecks_high_risk_confirmation_before_side_effect():
    descriptor = _descriptor(
        risk_level=RiskLevel.EXTERNAL_SIDE_EFFECT,
        confirmation_policy=ConfirmationPolicy.REQUIRED,
    )
    event = ProviderEvent.create(
        event_id="provider-succeeded",
        task_id="task-1",
        kind=ProviderEventKind.SUCCEEDED,
        occurred_at=102,
        result={"summary": "晴"},
    )
    provider = _Provider(descriptor, event)
    forged = _run(descriptor)
    adapter = AstrBotCapabilityAdapter((provider,))

    with pytest.raises(InvalidProviderEvent, match="confirmation"):
        adapter.start(forged)
    assert provider.start_calls == 0


def test_direct_dataclass_construction_cannot_bypass_contract_validation():
    forged_descriptor = CapabilityDescriptor(
        capability_id="calendar.delete",
        provider_id="astrbot.calendar",
        input_schema=(),
        output_schema=(),
        risk_level=RiskLevel.DESTRUCTIVE,
        required_scopes=("calendar:delete",),
        idempotent=False,
        cancellable=False,
        supports_progress=False,
        expected_latency_ms=1,
        media_output_kinds=(),
        confirmation_policy=ConfirmationPolicy.NEVER,
    )
    provider = _Provider(forged_descriptor, object())

    with pytest.raises(ValueError, match="confirmation"):
        AstrBotCapabilityAdapter((provider,))


def test_direct_malformed_event_construction_is_revalidated_at_boundary():
    descriptor = _descriptor()
    forged_event = ProviderEvent(
        event_id="",
        task_id="task-1",
        kind=ProviderEventKind.SUCCEEDED,
        occurred_at=102,
        result={"summary": "晴"},
    )
    adapter = AstrBotCapabilityAdapter((_Provider(descriptor, forged_event),))

    with pytest.raises(InvalidProviderEvent, match="event_id"):
        adapter.start(_run(descriptor))
