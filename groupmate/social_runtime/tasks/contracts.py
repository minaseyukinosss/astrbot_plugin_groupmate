"""Immutable contracts for governed, provider-backed capabilities."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class RiskLevel(str, Enum):
    READ_ONLY = "read_only"
    LOW_IMPACT = "low_impact"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    SENSITIVE = "sensitive"
    DESTRUCTIVE = "destructive"


class ConfirmationPolicy(str, Enum):
    NEVER = "never"
    REQUIRED = "required"


class TaskStatus(str, Enum):
    PROPOSED = "proposed"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class ProviderEventKind(str, Enum):
    ACCEPTED = "accepted"
    PROGRESS = "progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


_HIGH_RISK = frozenset(
    {
        RiskLevel.EXTERNAL_SIDE_EFFECT,
        RiskLevel.SENSITIVE,
        RiskLevel.DESTRUCTIVE,
    }
)
_FIELD_TYPES = frozenset(
    {"string", "integer", "number", "boolean", "object", "array", "null", "any"}
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _required_text(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    return text


def _json_mapping(value: Mapping[str, object], name: str) -> Mapping[str, object]:
    try:
        encoded = json.dumps(dict(value), ensure_ascii=False, sort_keys=True, allow_nan=False)
        normalized = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a JSON-serializable mapping") from exc
    return MappingProxyType(normalized)


@dataclass(frozen=True)
class CapabilityField:
    name: str
    type_name: str
    required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required_text(self.name, "field name"))
        normalized = _required_text(self.type_name, "field type").lower()
        if normalized not in _FIELD_TYPES:
            raise ValueError(f"unsupported field type: {normalized}")
        object.__setattr__(self, "type_name", normalized)


@dataclass(frozen=True)
class CapabilityDescriptor:
    capability_id: str
    provider_id: str
    input_schema: tuple[CapabilityField, ...]
    output_schema: tuple[CapabilityField, ...]
    risk_level: RiskLevel
    required_scopes: tuple[str, ...]
    idempotent: bool
    cancellable: bool
    supports_progress: bool
    expected_latency_ms: int
    media_output_kinds: tuple[str, ...]
    confirmation_policy: ConfirmationPolicy

    @classmethod
    def create(cls, **values: object) -> "CapabilityDescriptor":
        normalized = dict(values)
        normalized["capability_id"] = _required_text(
            normalized.get("capability_id"), "capability_id"
        )
        normalized["provider_id"] = _required_text(
            normalized.get("provider_id"), "provider_id"
        )
        normalized["input_schema"] = tuple(normalized.get("input_schema", ()))
        normalized["output_schema"] = tuple(normalized.get("output_schema", ()))
        for name in ("input_schema", "output_schema"):
            if not all(isinstance(field, CapabilityField) for field in normalized[name]):
                raise ValueError(f"{name} must contain CapabilityField values")
            names = tuple(field.name for field in normalized[name])
            if len(names) != len(set(names)):
                raise ValueError(f"{name} contains duplicate field names")
        normalized["risk_level"] = RiskLevel(normalized.get("risk_level"))
        scopes = tuple(
            _required_text(item, "required scope")
            for item in normalized.get("required_scopes", ())
        )
        if len(scopes) != len(set(scopes)):
            raise ValueError("required_scopes must not contain duplicates")
        normalized["required_scopes"] = scopes
        for name in ("idempotent", "cancellable", "supports_progress"):
            if not isinstance(normalized.get(name), bool):
                raise ValueError(f"{name} must be a boolean")
        latency = int(normalized.get("expected_latency_ms", -1))
        if latency < 0:
            raise ValueError("expected_latency_ms must not be negative")
        normalized["expected_latency_ms"] = latency
        media_kinds = tuple(
            _required_text(item, "media output kind").lower()
            for item in normalized.get("media_output_kinds", ())
        )
        if len(media_kinds) != len(set(media_kinds)):
            raise ValueError("media_output_kinds must not contain duplicates")
        normalized["media_output_kinds"] = media_kinds
        normalized["confirmation_policy"] = ConfirmationPolicy(
            normalized.get("confirmation_policy")
        )
        if (
            normalized["risk_level"] in _HIGH_RISK
            and normalized["confirmation_policy"] is not ConfirmationPolicy.REQUIRED
        ):
            raise ValueError("high-risk capabilities require confirmation")
        return cls(**normalized)

    @property
    def requires_confirmation(self) -> bool:
        return self.confirmation_policy is ConfirmationPolicy.REQUIRED


@dataclass(frozen=True)
class CapabilityRequest:
    requester_id: str
    persona_id: str
    group_id: str
    topic_id: str | None
    input_payload: Mapping[str, object]
    authorization_scopes: tuple[str, ...]
    idempotency_key: str
    correlation_id: str
    expires_at: int

    @classmethod
    def create(cls, **values: object) -> "CapabilityRequest":
        normalized = dict(values)
        for name in (
            "requester_id",
            "persona_id",
            "group_id",
            "idempotency_key",
            "correlation_id",
        ):
            normalized[name] = _required_text(normalized.get(name), name)
        topic = normalized.get("topic_id")
        normalized["topic_id"] = None if topic is None else _required_text(topic, "topic_id")
        payload = normalized.get("input_payload", {})
        if not isinstance(payload, Mapping):
            raise ValueError("input_payload must be a mapping")
        normalized["input_payload"] = _json_mapping(payload, "input_payload")
        scopes = tuple(
            _required_text(item, "authorization scope")
            for item in normalized.get("authorization_scopes", ())
        )
        if len(scopes) != len(set(scopes)):
            raise ValueError("authorization_scopes must not contain duplicates")
        normalized["authorization_scopes"] = scopes
        expires_at = int(normalized.get("expires_at", -1))
        if expires_at < 0:
            raise ValueError("expires_at must not be negative")
        normalized["expires_at"] = expires_at
        return cls(**normalized)


@dataclass(frozen=True)
class ProviderMedia:
    media_id: str
    kind: str
    uri: str
    mime_type: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        for name in ("media_id", "kind", "uri", "mime_type", "sha256"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        object.__setattr__(self, "kind", self.kind.lower())
        object.__setattr__(self, "mime_type", self.mime_type.lower())
        digest = self.sha256.lower()
        if not _SHA256.fullmatch(digest):
            raise ValueError("sha256 must be a lowercase hexadecimal digest")
        object.__setattr__(self, "sha256", digest)
        if self.size_bytes <= 0:
            raise ValueError("size_bytes must be positive")
        if not self.mime_type.startswith(f"{self.kind}/"):
            raise ValueError("media kind and MIME type do not match")


@dataclass(frozen=True)
class ProviderEvent:
    event_id: str
    task_id: str
    kind: ProviderEventKind
    occurred_at: int
    progress: int | None = None
    result: Mapping[str, object] | None = None
    media: tuple[ProviderMedia, ...] = ()
    error_code: str | None = None

    @classmethod
    def create(cls, **values: object) -> "ProviderEvent":
        normalized = dict(values)
        normalized["event_id"] = _required_text(normalized.get("event_id"), "event_id")
        normalized["task_id"] = _required_text(normalized.get("task_id"), "task_id")
        normalized["kind"] = ProviderEventKind(normalized.get("kind"))
        occurred_at = int(normalized.get("occurred_at", -1))
        if occurred_at < 0:
            raise ValueError("occurred_at must not be negative")
        normalized["occurred_at"] = occurred_at
        progress = normalized.get("progress")
        if progress is not None:
            progress = int(progress)
            if not 0 <= progress <= 100:
                raise ValueError("progress must be between 0 and 100")
        if normalized["kind"] is ProviderEventKind.PROGRESS and progress is None:
            raise ValueError("progress event requires progress")
        normalized["progress"] = progress
        result = normalized.get("result")
        if result is not None:
            if not isinstance(result, Mapping):
                raise ValueError("result must be a mapping")
            result = _json_mapping(result, "result")
        if normalized["kind"] is ProviderEventKind.SUCCEEDED and result is None:
            raise ValueError("succeeded event requires result")
        normalized["result"] = result
        media = tuple(normalized.get("media", ()))
        if not all(isinstance(item, ProviderMedia) for item in media):
            raise ValueError("media must contain ProviderMedia values")
        normalized["media"] = media
        if normalized["kind"] is not ProviderEventKind.SUCCEEDED and (result is not None or media):
            raise ValueError("only succeeded events may carry result or media")
        error = normalized.get("error_code")
        normalized["error_code"] = None if error is None else _required_text(error, "error_code")
        if normalized["kind"] is ProviderEventKind.FAILED and not normalized["error_code"]:
            raise ValueError("failed event requires error_code")
        return cls(**normalized)


@dataclass(frozen=True)
class TaskRun:
    task_id: str
    descriptor: CapabilityDescriptor
    request: CapabilityRequest
    status: TaskStatus
    progress: int | None
    result: Mapping[str, object] | None
    result_media: tuple[ProviderMedia, ...]
    error_code: str | None
    delivery_relevant: bool
    confirmed_by: str | None
    version: int
    updated_at: int

    @classmethod
    def proposed(
        cls,
        *,
        task_id: str,
        descriptor: CapabilityDescriptor,
        request: CapabilityRequest,
        now: int,
    ) -> "TaskRun":
        return cls(
            task_id=_required_text(task_id, "task_id"),
            descriptor=descriptor,
            request=request,
            status=TaskStatus.PROPOSED,
            progress=None,
            result=None,
            result_media=(),
            error_code=None,
            delivery_relevant=True,
            confirmed_by=None,
            version=1,
            updated_at=int(now),
        )

    @classmethod
    def restore(
        cls,
        *,
        task_id: str,
        descriptor: CapabilityDescriptor,
        request: CapabilityRequest,
        status: TaskStatus,
        progress: int | None,
        result: Mapping[str, object] | None,
        result_media: tuple[ProviderMedia, ...],
        error_code: str | None,
        delivery_relevant: bool,
        confirmed_by: str | None,
        version: int,
        updated_at: int,
    ) -> "TaskRun":
        normalized_progress = None if progress is None else int(progress)
        if normalized_progress is not None and not 0 <= normalized_progress <= 100:
            raise ValueError("task progress must be between 0 and 100")
        normalized_result = None
        if result is not None:
            if not isinstance(result, Mapping):
                raise ValueError("task result must be a mapping")
            normalized_result = _json_mapping(result, "task result")
        media = tuple(result_media)
        if not all(isinstance(item, ProviderMedia) for item in media):
            raise ValueError("task result_media must contain ProviderMedia values")
        if not isinstance(delivery_relevant, bool):
            raise ValueError("delivery_relevant must be a boolean")
        normalized_version = int(version)
        if normalized_version < 1:
            raise ValueError("task version must be positive")
        normalized_updated_at = int(updated_at)
        if normalized_updated_at < 0:
            raise ValueError("task updated_at must not be negative")
        return cls(
            task_id=_required_text(task_id, "task_id"),
            descriptor=normalize_descriptor(descriptor),
            request=normalize_request(request),
            status=TaskStatus(status),
            progress=normalized_progress,
            result=normalized_result,
            result_media=media,
            error_code=(
                None if error_code is None else _required_text(error_code, "error_code")
            ),
            delivery_relevant=delivery_relevant,
            confirmed_by=(
                None
                if confirmed_by is None
                else _required_text(confirmed_by, "confirmed_by")
            ),
            version=normalized_version,
            updated_at=normalized_updated_at,
        )

    def transition(self, status: TaskStatus, *, now: int, **changes: object) -> "TaskRun":
        return replace(
            self,
            status=TaskStatus(status),
            version=self.version + 1,
            updated_at=int(now),
            **changes,
        )

    @property
    def capability_id(self) -> str:
        return self.descriptor.capability_id

    @property
    def provider_id(self) -> str:
        return self.descriptor.provider_id

    @property
    def requester_id(self) -> str:
        return self.request.requester_id

    @property
    def persona_id(self) -> str:
        return self.request.persona_id

    @property
    def group_id(self) -> str:
        return self.request.group_id

    @property
    def topic_id(self) -> str | None:
        return self.request.topic_id

    @property
    def input_payload(self) -> Mapping[str, object]:
        return self.request.input_payload

    @property
    def authorization_scopes(self) -> tuple[str, ...]:
        return self.request.authorization_scopes

    @property
    def idempotency_key(self) -> str:
        return self.request.idempotency_key

    @property
    def correlation_id(self) -> str:
        return self.request.correlation_id

    @property
    def expires_at(self) -> int:
        return self.request.expires_at


def validate_payload(
    payload: Mapping[str, object],
    schema: tuple[CapabilityField, ...],
    *,
    label: str,
) -> None:
    fields = {field.name: field for field in schema}
    unknown = sorted(set(payload) - set(fields))
    if unknown:
        raise ValueError(f"{label} contains undeclared field: {unknown[0]}")
    for field in schema:
        if field.required and field.name not in payload:
            raise ValueError(f"{label} is missing required field: {field.name}")
        if field.name not in payload or field.type_name == "any":
            continue
        value = payload[field.name]
        valid = {
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "object": isinstance(value, Mapping),
            "array": isinstance(value, list),
            "null": value is None,
        }[field.type_name]
        if not valid:
            raise ValueError(f"{label} field {field.name} must be {field.type_name}")


def normalize_descriptor(value: CapabilityDescriptor) -> CapabilityDescriptor:
    if not isinstance(value, CapabilityDescriptor):
        raise ValueError("descriptor must be a CapabilityDescriptor")
    return CapabilityDescriptor.create(
        capability_id=value.capability_id,
        provider_id=value.provider_id,
        input_schema=value.input_schema,
        output_schema=value.output_schema,
        risk_level=value.risk_level,
        required_scopes=value.required_scopes,
        idempotent=value.idempotent,
        cancellable=value.cancellable,
        supports_progress=value.supports_progress,
        expected_latency_ms=value.expected_latency_ms,
        media_output_kinds=value.media_output_kinds,
        confirmation_policy=value.confirmation_policy,
    )


def normalize_request(value: CapabilityRequest) -> CapabilityRequest:
    if not isinstance(value, CapabilityRequest):
        raise ValueError("request must be a CapabilityRequest")
    return CapabilityRequest.create(
        requester_id=value.requester_id,
        persona_id=value.persona_id,
        group_id=value.group_id,
        topic_id=value.topic_id,
        input_payload=value.input_payload,
        authorization_scopes=value.authorization_scopes,
        idempotency_key=value.idempotency_key,
        correlation_id=value.correlation_id,
        expires_at=value.expires_at,
    )


def normalize_provider_event(value: ProviderEvent) -> ProviderEvent:
    if not isinstance(value, ProviderEvent):
        raise ValueError("provider must return a ProviderEvent")
    return ProviderEvent.create(
        event_id=value.event_id,
        task_id=value.task_id,
        kind=value.kind,
        occurred_at=value.occurred_at,
        progress=value.progress,
        result=value.result,
        media=value.media,
        error_code=value.error_code,
    )


__all__ = (
    "CapabilityDescriptor",
    "CapabilityField",
    "CapabilityRequest",
    "ConfirmationPolicy",
    "ProviderEvent",
    "ProviderEventKind",
    "ProviderMedia",
    "RiskLevel",
    "TaskRun",
    "TaskStatus",
    "normalize_descriptor",
    "normalize_provider_event",
    "normalize_request",
    "validate_payload",
)
