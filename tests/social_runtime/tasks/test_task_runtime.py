from __future__ import annotations

import pytest

from groupmate.social_runtime.tasks.contracts import (
    CapabilityDescriptor,
    CapabilityField,
    CapabilityRequest,
    ConfirmationPolicy,
    ProviderEvent,
    ProviderEventKind,
    ProviderMedia,
    RiskLevel,
    TaskStatus,
)
from groupmate.social_runtime.tasks.runtime import (
    InvalidTaskTransition,
    TaskIdentityConflict,
    TaskRuntime,
    UnauthorizedCapability,
)


def _descriptor(**overrides) -> CapabilityDescriptor:
    values = {
        "capability_id": "calendar.create",
        "provider_id": "astrbot.calendar",
        "input_schema": (CapabilityField("title", "string"),),
        "output_schema": (CapabilityField("event_id", "string"),),
        "risk_level": RiskLevel.EXTERNAL_SIDE_EFFECT,
        "required_scopes": ("calendar:write",),
        "idempotent": True,
        "cancellable": True,
        "supports_progress": True,
        "expected_latency_ms": 2_000,
        "media_output_kinds": (),
        "confirmation_policy": ConfirmationPolicy.REQUIRED,
    }
    values.update(overrides)
    return CapabilityDescriptor.create(**values)


def _request(**overrides) -> CapabilityRequest:
    values = {
        "requester_id": "user-1",
        "persona_id": "persona-1",
        "group_id": "group-1",
        "topic_id": "topic-plans",
        "input_payload": {"title": "周五聚餐"},
        "authorization_scopes": ("calendar:write",),
        "idempotency_key": "calendar-request-1",
        "correlation_id": "corr-1",
        "expires_at": 500,
    }
    values.update(overrides)
    return CapabilityRequest.create(**values)


def _running(runtime: TaskRuntime):
    proposed = runtime.propose(_descriptor(), _request(), now=100)
    awaiting = runtime.start(proposed.task_id, now=101)
    queued = runtime.confirm(proposed.task_id, confirmer_id="user-1", now=102)
    running = runtime.start(proposed.task_id, now=103)
    return proposed, awaiting, queued, running


def test_persistent_confirmed_task_follows_the_legal_state_machine(tmp_path):
    path = tmp_path / "social-runtime.db"
    runtime = TaskRuntime(path)

    proposed, awaiting, queued, running = _running(runtime)
    succeeded = runtime.apply_event(
        ProviderEvent.create(
            event_id="provider-success-1",
            task_id=running.task_id,
            kind=ProviderEventKind.SUCCEEDED,
            occurred_at=104,
            result={"event_id": "calendar-event-8"},
        )
    )

    assert [proposed.status, awaiting.status, queued.status, running.status, succeeded.status] == [
        TaskStatus.PROPOSED,
        TaskStatus.AWAITING_CONFIRMATION,
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        TaskStatus.SUCCEEDED,
    ]
    reopened = TaskRuntime(path).load(succeeded.task_id)
    assert reopened == succeeded
    assert reopened.requester_id == "user-1"
    assert reopened.group_id == "group-1"
    assert reopened.topic_id == "topic-plans"
    assert dict(reopened.input_payload) == {"title": "周五聚餐"}
    assert reopened.authorization_scopes == ("calendar:write",)
    assert reopened.idempotency_key == "calendar-request-1"
    assert reopened.provider_id == "astrbot.calendar"


def test_terminal_task_cannot_be_started_again(tmp_path):
    runtime = TaskRuntime(tmp_path / "social-runtime.db")
    *_, running = _running(runtime)
    runtime.apply_event(
        ProviderEvent.create(
            event_id="provider-success-1",
            task_id=running.task_id,
            kind=ProviderEventKind.SUCCEEDED,
            occurred_at=104,
            result={"event_id": "calendar-event-8"},
        )
    )

    with pytest.raises(InvalidTaskTransition, match="SUCCEEDED"):
        runtime.start(running.task_id, now=105)


def test_duplicate_provider_event_is_idempotent(tmp_path):
    runtime = TaskRuntime(tmp_path / "social-runtime.db")
    *_, running = _running(runtime)
    event = ProviderEvent.create(
        event_id="provider-progress-1",
        task_id=running.task_id,
        kind=ProviderEventKind.PROGRESS,
        occurred_at=104,
        progress=40,
    )

    first = runtime.apply_event(event)
    duplicate = runtime.apply_event(event)

    assert duplicate == first
    assert duplicate.version == running.version + 1
    assert runtime.event_count(running.task_id) == 5


def test_idempotency_key_cannot_be_reused_for_different_input(tmp_path):
    runtime = TaskRuntime(tmp_path / "social-runtime.db")
    runtime.propose(_descriptor(), _request(), now=100)

    with pytest.raises(TaskIdentityConflict, match="idempotency"):
        runtime.propose(
            _descriptor(),
            _request(input_payload={"title": "不同事项"}),
            now=101,
        )


def test_relationship_context_does_not_replace_authorization(tmp_path):
    runtime = TaskRuntime(tmp_path / "social-runtime.db")
    request = _request(
        authorization_scopes=(),
        input_payload={"title": "周五聚餐", "relationship": "best_friend"},
    )

    with pytest.raises(UnauthorizedCapability, match="calendar:write"):
        runtime.propose(_descriptor(), request, now=100)


@pytest.mark.parametrize(
    "risk",
    (
        RiskLevel.EXTERNAL_SIDE_EFFECT,
        RiskLevel.SENSITIVE,
        RiskLevel.DESTRUCTIVE,
    ),
)
def test_high_risk_tasks_must_wait_for_requester_confirmation(tmp_path, risk):
    runtime = TaskRuntime(tmp_path / f"{risk.value}.db")
    descriptor = _descriptor(risk_level=risk)
    proposed = runtime.propose(descriptor, _request(), now=100)

    assert runtime.start(proposed.task_id, now=101).status == TaskStatus.AWAITING_CONFIRMATION
    with pytest.raises(UnauthorizedCapability, match="requester"):
        runtime.confirm(proposed.task_id, confirmer_id="user-2", now=102)


def test_expired_task_never_starts(tmp_path):
    runtime = TaskRuntime(tmp_path / "social-runtime.db")
    proposed = runtime.propose(_descriptor(), _request(expires_at=101), now=100)

    assert runtime.start(proposed.task_id, now=101).status == TaskStatus.EXPIRED


def test_runtime_revalidates_provider_media_before_persisting_success(tmp_path):
    runtime = TaskRuntime(tmp_path / "social-runtime.db")
    *_, running = _running(runtime)
    event = ProviderEvent.create(
        event_id="provider-success-with-media",
        task_id=running.task_id,
        kind=ProviderEventKind.SUCCEEDED,
        occurred_at=104,
        result={"event_id": "calendar-event-8"},
        media=(
            ProviderMedia(
                media_id="undeclared-card",
                kind="image",
                uri="provider://astrbot.calendar/card",
                mime_type="image/png",
                size_bytes=100,
                sha256="c" * 64,
            ),
        ),
    )

    with pytest.raises(InvalidTaskTransition, match="media kind"):
        runtime.apply_event(event)
    assert runtime.load(running.task_id).status == TaskStatus.RUNNING
