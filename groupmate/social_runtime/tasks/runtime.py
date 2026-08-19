"""SQLite-backed state machine for governed capability tasks."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from pathlib import Path
from typing import Iterable, Mapping

from ..persistence.schema import connect_database, initialize_database
from .contracts import (
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
    normalize_descriptor,
    normalize_provider_event,
    normalize_request,
    validate_payload,
)


class TaskNotFound(LookupError):
    """Raised when a task id does not exist in this runtime."""


class InvalidTaskTransition(RuntimeError):
    """Raised when a caller attempts an illegal state transition."""


class TaskIdentityConflict(RuntimeError):
    """Raised when an idempotency key is reused for different content."""


class ProviderEventConflict(RuntimeError):
    """Raised when a provider event id is reused for different content."""


class TaskVersionConflict(RuntimeError):
    """Raised when another worker advanced a task first."""


class UnauthorizedCapability(PermissionError):
    """Raised when explicit authorization does not cover provider scopes."""


_TERMINAL = frozenset(
    {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELED,
        TaskStatus.EXPIRED,
        TaskStatus.UNKNOWN,
    }
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _field_to_dict(field: CapabilityField) -> dict[str, object]:
    return {
        "name": field.name,
        "type_name": field.type_name,
        "required": field.required,
    }


def _descriptor_to_dict(descriptor: CapabilityDescriptor) -> dict[str, object]:
    return {
        "capability_id": descriptor.capability_id,
        "provider_id": descriptor.provider_id,
        "input_schema": [_field_to_dict(item) for item in descriptor.input_schema],
        "output_schema": [_field_to_dict(item) for item in descriptor.output_schema],
        "risk_level": descriptor.risk_level.value,
        "required_scopes": list(descriptor.required_scopes),
        "idempotent": descriptor.idempotent,
        "cancellable": descriptor.cancellable,
        "supports_progress": descriptor.supports_progress,
        "expected_latency_ms": descriptor.expected_latency_ms,
        "media_output_kinds": list(descriptor.media_output_kinds),
        "confirmation_policy": descriptor.confirmation_policy.value,
    }


def _descriptor_from_dict(value: Mapping[str, object]) -> CapabilityDescriptor:
    return CapabilityDescriptor.create(
        capability_id=value["capability_id"],
        provider_id=value["provider_id"],
        input_schema=tuple(CapabilityField(**item) for item in value["input_schema"]),
        output_schema=tuple(CapabilityField(**item) for item in value["output_schema"]),
        risk_level=RiskLevel(value["risk_level"]),
        required_scopes=tuple(value["required_scopes"]),
        idempotent=value["idempotent"],
        cancellable=value["cancellable"],
        supports_progress=value["supports_progress"],
        expected_latency_ms=value["expected_latency_ms"],
        media_output_kinds=tuple(value["media_output_kinds"]),
        confirmation_policy=ConfirmationPolicy(value["confirmation_policy"]),
    )


def _request_to_dict(request: CapabilityRequest) -> dict[str, object]:
    return {
        "requester_id": request.requester_id,
        "persona_id": request.persona_id,
        "group_id": request.group_id,
        "topic_id": request.topic_id,
        "input_payload": dict(request.input_payload),
        "authorization_scopes": list(request.authorization_scopes),
        "idempotency_key": request.idempotency_key,
        "correlation_id": request.correlation_id,
        "expires_at": request.expires_at,
        "direct_request": request.direct_request,
    }


def _request_from_dict(value: Mapping[str, object]) -> CapabilityRequest:
    return CapabilityRequest.create(
        requester_id=value["requester_id"],
        persona_id=value["persona_id"],
        group_id=value["group_id"],
        topic_id=value.get("topic_id"),
        input_payload=value["input_payload"],
        authorization_scopes=tuple(value["authorization_scopes"]),
        idempotency_key=value["idempotency_key"],
        correlation_id=value["correlation_id"],
        expires_at=value["expires_at"],
        direct_request=value.get("direct_request", False),
    )


def _media_to_dict(media: ProviderMedia) -> dict[str, object]:
    return {
        "media_id": media.media_id,
        "kind": media.kind,
        "uri": media.uri,
        "mime_type": media.mime_type,
        "size_bytes": media.size_bytes,
        "sha256": media.sha256,
    }


def _task_to_dict(task: TaskRun) -> dict[str, object]:
    return {
        "task_id": task.task_id,
        "descriptor": _descriptor_to_dict(task.descriptor),
        "request": _request_to_dict(task.request),
        "status": task.status.value,
        "progress": task.progress,
        "result": None if task.result is None else dict(task.result),
        "result_media": [_media_to_dict(item) for item in task.result_media],
        "error_code": task.error_code,
        "delivery_relevant": task.delivery_relevant,
        "confirmed_by": task.confirmed_by,
        "version": task.version,
        "updated_at": task.updated_at,
    }


def _task_from_dict(value: Mapping[str, object]) -> TaskRun:
    return TaskRun.restore(
        task_id=value["task_id"],
        descriptor=_descriptor_from_dict(value["descriptor"]),
        request=_request_from_dict(value["request"]),
        status=TaskStatus(value["status"]),
        progress=value.get("progress"),
        result=value.get("result"),
        result_media=tuple(
            ProviderMedia(**item) for item in value.get("result_media", ())
        ),
        error_code=value.get("error_code"),
        delivery_relevant=value["delivery_relevant"],
        confirmed_by=value.get("confirmed_by"),
        version=value["version"],
        updated_at=value["updated_at"],
    )


def _event_to_dict(event: ProviderEvent) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "task_id": event.task_id,
        "kind": event.kind.value,
        "occurred_at": event.occurred_at,
        "progress": event.progress,
        "result": None if event.result is None else dict(event.result),
        "media": [_media_to_dict(item) for item in event.media],
        "error_code": event.error_code,
    }


def _event_from_dict(value: Mapping[str, object]) -> ProviderEvent:
    return ProviderEvent.create(
        event_id=value["event_id"],
        task_id=value["task_id"],
        kind=ProviderEventKind(value["kind"]),
        occurred_at=value["occurred_at"],
        progress=value.get("progress"),
        result=value.get("result"),
        media=tuple(ProviderMedia(**item) for item in value.get("media", ())),
        error_code=value.get("error_code"),
    )


class TaskRuntime:
    """Persists every state before downstream execution can observe it."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        initialize_database(self.path)

    def propose(
        self,
        descriptor: CapabilityDescriptor,
        request: CapabilityRequest,
        *,
        now: int,
    ) -> TaskRun:
        descriptor = normalize_descriptor(descriptor)
        request = normalize_request(request)
        missing = sorted(set(descriptor.required_scopes) - set(request.authorization_scopes))
        if missing:
            raise UnauthorizedCapability(
                f"missing explicit capability scope: {missing[0]}"
            )
        validate_payload(request.input_payload, descriptor.input_schema, label="capability input")
        if request.expires_at <= now:
            raise ValueError("task must expire after it is proposed")
        task_id = self._task_id(descriptor, request)
        proposed = TaskRun.proposed(
            task_id=task_id,
            descriptor=descriptor,
            request=request,
            now=now,
        )
        encoded = _canonical_json(_task_to_dict(proposed))
        event_id = f"runtime:{task_id}:proposed"
        with closing(connect_database(self.path)) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT task_json FROM tasks WHERE task_id=?", (task_id,)
                ).fetchone()
                if row is not None:
                    existing = _task_from_dict(json.loads(row["task_json"]))
                    if (
                        existing.descriptor != descriptor
                        or existing.request != request
                    ):
                        raise TaskIdentityConflict(
                            "idempotency key already belongs to different task content"
                        )
                    db.commit()
                    return existing
                db.execute(
                    "INSERT INTO tasks(task_id, correlation_id, persona_id, group_id, "
                    "status, task_json, version, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        task_id,
                        request.correlation_id,
                        request.persona_id,
                        request.group_id,
                        proposed.status.value,
                        encoded,
                        proposed.version,
                        proposed.updated_at,
                    ),
                )
                db.execute(
                    "INSERT INTO task_events(event_id, task_id, event_type, event_json, "
                    "occurred_at) VALUES(?, ?, ?, ?, ?)",
                    (event_id, task_id, "task.proposed", encoded, now),
                )
                db.commit()
                return proposed
            except BaseException:
                db.rollback()
                raise

    def load(self, task_id: str) -> TaskRun:
        with closing(connect_database(self.path)) as db:
            row = db.execute(
                "SELECT task_json FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        if row is None:
            raise TaskNotFound(task_id)
        return _task_from_dict(json.loads(row["task_json"]))

    def start(self, task_id: str, *, now: int) -> TaskRun:
        task = self.load(task_id)
        if task.status in _TERMINAL:
            raise InvalidTaskTransition(f"cannot start task from {task.status.name}")
        if now >= task.expires_at:
            return self._transition(
                task,
                TaskStatus.EXPIRED,
                now=now,
                event_type="task.expired",
            )
        if task.status is TaskStatus.PROPOSED:
            target = (
                TaskStatus.AWAITING_CONFIRMATION
                if task.descriptor.requires_confirmation
                else TaskStatus.QUEUED
            )
            return self._transition(
                task,
                target,
                now=now,
                event_type=f"task.{target.value}",
            )
        if task.status is TaskStatus.QUEUED:
            return self._transition(
                task,
                TaskStatus.RUNNING,
                now=now,
                event_type="task.started",
            )
        raise InvalidTaskTransition(f"cannot start task from {task.status.name}")

    def confirm(self, task_id: str, *, confirmer_id: str, now: int) -> TaskRun:
        task = self.load(task_id)
        if task.status is not TaskStatus.AWAITING_CONFIRMATION:
            raise InvalidTaskTransition(
                f"cannot confirm task from {task.status.name}"
            )
        if str(confirmer_id).strip() != task.requester_id:
            raise UnauthorizedCapability("task confirmation must come from requester")
        if now >= task.expires_at:
            return self._transition(
                task,
                TaskStatus.EXPIRED,
                now=now,
                event_type="task.expired",
            )
        return self._transition(
            task,
            TaskStatus.QUEUED,
            now=now,
            event_type="task.confirmed",
            confirmed_by=task.requester_id,
        )

    def apply_event(self, event: ProviderEvent) -> TaskRun:
        try:
            event = normalize_provider_event(event)
        except ValueError as exc:
            raise InvalidTaskTransition(str(exc)) from exc
        encoded_event = _canonical_json(_event_to_dict(event))
        with closing(connect_database(self.path)) as db:
            existing_event = db.execute(
                "SELECT task_id, event_json FROM task_events WHERE event_id=?",
                (event.event_id,),
            ).fetchone()
        if existing_event is not None:
            if (
                existing_event["task_id"] != event.task_id
                or existing_event["event_json"] != encoded_event
            ):
                raise ProviderEventConflict(
                    f"provider event id was reused: {event.event_id}"
                )
            return self.load(event.task_id)

        task = self.load(event.task_id)
        changes: dict[str, object] = {}
        if event.kind is ProviderEventKind.ACCEPTED:
            if task.status not in (TaskStatus.QUEUED, TaskStatus.RUNNING):
                self._invalid_event(task, event)
            target = TaskStatus.RUNNING
        elif event.kind is ProviderEventKind.PROGRESS:
            if task.status is not TaskStatus.RUNNING:
                self._invalid_event(task, event)
            if not task.descriptor.supports_progress:
                raise InvalidTaskTransition("provider contract does not support progress")
            if task.progress is not None and event.progress < task.progress:
                raise InvalidTaskTransition("provider progress must be monotonic")
            target = TaskStatus.RUNNING
            changes["progress"] = event.progress
        elif event.kind is ProviderEventKind.SUCCEEDED:
            if task.status is not TaskStatus.RUNNING:
                self._invalid_event(task, event)
            assert event.result is not None
            validate_payload(
                event.result,
                task.descriptor.output_schema,
                label="capability output",
            )
            allowed_media = frozenset(task.descriptor.media_output_kinds)
            for media in event.media:
                if media.kind not in allowed_media:
                    raise InvalidTaskTransition(
                        f"provider returned undeclared media kind: {media.kind}"
                    )
            target = TaskStatus.SUCCEEDED
            changes.update(
                result=event.result,
                result_media=event.media,
                progress=100,
                error_code=None,
            )
        elif event.kind is ProviderEventKind.FAILED:
            if task.status is not TaskStatus.RUNNING:
                self._invalid_event(task, event)
            target = TaskStatus.FAILED
            changes["error_code"] = event.error_code
        else:
            if task.status not in (TaskStatus.QUEUED, TaskStatus.RUNNING):
                self._invalid_event(task, event)
            target = TaskStatus.CANCELED
        updated = task.transition(target, now=event.occurred_at, **changes)
        return self._save(
            task,
            updated,
            event_id=event.event_id,
            event_type=f"provider.{event.kind.value}",
            event_json=encoded_event,
            occurred_at=event.occurred_at,
        )

    def cancel(self, task_id: str, *, now: int) -> TaskRun:
        task = self.load(task_id)
        if task.status is TaskStatus.RUNNING:
            if not task.descriptor.cancellable:
                raise InvalidTaskTransition("running task is not cancellable")
            raise InvalidTaskTransition(
                "running task requires a structured provider cancellation event"
            )
        if task.status not in (
            TaskStatus.PROPOSED,
            TaskStatus.AWAITING_CONFIRMATION,
            TaskStatus.QUEUED,
        ):
            raise InvalidTaskTransition(f"cannot cancel task from {task.status.name}")
        return self._transition(
            task,
            TaskStatus.CANCELED,
            now=now,
            event_type="task.canceled",
        )

    def expire(self, task_id: str, *, now: int) -> TaskRun:
        task = self.load(task_id)
        if task.status in _TERMINAL:
            return task
        if now < task.expires_at:
            raise InvalidTaskTransition("task has not expired")
        return self._transition(
            task,
            TaskStatus.EXPIRED,
            now=now,
            event_type="task.expired",
        )

    def recover(self, adapter: object, *, now: int) -> tuple[TaskRun, ...]:
        recovered = []
        for task in self._list_status(TaskStatus.RUNNING):
            if now >= task.expires_at:
                recovered.append(self.expire(task.task_id, now=now))
                continue
            if not task.descriptor.idempotent:
                recovered.append(
                    self._mark_unknown(
                        task,
                        now=now,
                        error_code="provider_status_not_safe_to_query",
                    )
                )
                continue
            try:
                if not adapter.can_query(task):
                    recovered.append(
                        self._mark_unknown(
                            task,
                            now=now,
                            error_code="provider_status_unqueryable",
                        )
                    )
                    continue
                event = adapter.query_status(task)
            except Exception:
                recovered.append(
                    self._mark_unknown(
                        task,
                        now=now,
                        error_code="provider_status_query_failed",
                    )
                )
                continue
            if event is None:
                recovered.append(
                    self._mark_unknown(
                        task,
                        now=now,
                        error_code="provider_status_unqueryable",
                    )
                )
            else:
                recovered.append(self.apply_event(event))
        return tuple(recovered)

    def event_count(self, task_id: str) -> int:
        with closing(connect_database(self.path)) as db:
            row = db.execute(
                "SELECT COUNT(*) AS count FROM task_events WHERE task_id=?",
                (task_id,),
            ).fetchone()
        return int(row["count"])

    def provider_events(self) -> tuple[ProviderEvent, ...]:
        with closing(connect_database(self.path)) as db:
            rows = db.execute(
                "SELECT event_json FROM task_events "
                "WHERE event_type LIKE 'provider.%' ORDER BY rowid"
            ).fetchall()
        return tuple(
            _event_from_dict(json.loads(row["event_json"])) for row in rows
        )

    def _list_status(self, status: TaskStatus) -> tuple[TaskRun, ...]:
        with closing(connect_database(self.path)) as db:
            rows = db.execute(
                "SELECT task_json FROM tasks WHERE status=? ORDER BY task_id",
                (status.value,),
            ).fetchall()
        return tuple(_task_from_dict(json.loads(row["task_json"])) for row in rows)

    def _mark_unknown(self, task: TaskRun, *, now: int, error_code: str) -> TaskRun:
        return self._transition(
            task,
            TaskStatus.UNKNOWN,
            now=now,
            event_type="task.recovery_unknown",
            error_code=error_code,
        )

    def _transition(
        self,
        task: TaskRun,
        target: TaskStatus,
        *,
        now: int,
        event_type: str,
        **changes: object,
    ) -> TaskRun:
        updated = task.transition(target, now=now, **changes)
        event_id = f"runtime:{task.task_id}:{updated.version}:{target.value}"
        return self._save(
            task,
            updated,
            event_id=event_id,
            event_type=event_type,
            event_json=_canonical_json(_task_to_dict(updated)),
            occurred_at=now,
        )

    def _save(
        self,
        previous: TaskRun,
        updated: TaskRun,
        *,
        event_id: str,
        event_type: str,
        event_json: str,
        occurred_at: int,
    ) -> TaskRun:
        encoded_task = _canonical_json(_task_to_dict(updated))
        with closing(connect_database(self.path)) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                changed = db.execute(
                    "UPDATE tasks SET status=?, task_json=?, version=?, updated_at=? "
                    "WHERE task_id=? AND version=?",
                    (
                        updated.status.value,
                        encoded_task,
                        updated.version,
                        updated.updated_at,
                        updated.task_id,
                        previous.version,
                    ),
                ).rowcount
                if changed != 1:
                    raise TaskVersionConflict(
                        f"task advanced concurrently: {updated.task_id}"
                    )
                db.execute(
                    "INSERT INTO task_events(event_id, task_id, event_type, event_json, "
                    "occurred_at) VALUES(?, ?, ?, ?, ?)",
                    (event_id, updated.task_id, event_type, event_json, occurred_at),
                )
                db.commit()
                return updated
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _task_id(
        descriptor: CapabilityDescriptor, request: CapabilityRequest
    ) -> str:
        identity = _canonical_json(
            {
                "provider_id": descriptor.provider_id,
                "capability_id": descriptor.capability_id,
                "persona_id": request.persona_id,
                "group_id": request.group_id,
                "requester_id": request.requester_id,
                "idempotency_key": request.idempotency_key,
            }
        )
        return "task:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()

    @staticmethod
    def _invalid_event(task: TaskRun, event: ProviderEvent) -> None:
        raise InvalidTaskTransition(
            f"cannot apply {event.kind.name} to {task.status.name}"
        )


__all__ = (
    "InvalidTaskTransition",
    "ProviderEventConflict",
    "TaskIdentityConflict",
    "TaskNotFound",
    "TaskRuntime",
    "TaskVersionConflict",
    "UnauthorizedCapability",
)
