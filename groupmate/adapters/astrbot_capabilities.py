"""Strict Provider Contract boundary for AstrBot capabilities.

This adapter deliberately accepts only typed provider events.  Human-readable bot
messages are social input, never execution receipts.
"""

from __future__ import annotations

from typing import Iterable, Protocol

from ..social_runtime.tasks.contracts import (
    CapabilityDescriptor,
    ProviderEvent,
    ProviderEventKind,
    TaskRun,
    TaskStatus,
    RiskLevel,
    normalize_descriptor,
    normalize_provider_event,
    normalize_request,
    validate_payload,
)


class ProviderNotRegistered(LookupError):
    """Raised when no exact Provider Contract owns a requested capability."""


class InvalidProviderEvent(ValueError):
    """Raised before an untrusted provider result can enter Task Runtime."""


class CapabilityProvider(Protocol):
    descriptor: CapabilityDescriptor

    def start(self, task: TaskRun) -> ProviderEvent: ...

    def cancel(self, task: TaskRun) -> ProviderEvent: ...


class AstrBotCapabilityAdapter:
    """Registers explicit providers and validates every event at the boundary."""

    def __init__(
        self,
        providers: Iterable[CapabilityProvider] = (),
        *,
        autonomous_allowlist: Iterable[tuple[str, str]] = (),
    ) -> None:
        self._providers: dict[tuple[str, str], CapabilityProvider] = {}
        self._autonomous_allowlist = frozenset(
            (str(provider_id).strip(), str(capability_id).strip())
            for provider_id, capability_id in autonomous_allowlist
            if str(provider_id).strip() and str(capability_id).strip()
        )
        for provider in providers:
            self.register(provider)

    def register(self, provider: CapabilityProvider) -> None:
        descriptor = getattr(provider, "descriptor", None)
        descriptor = normalize_descriptor(descriptor)
        key = (descriptor.provider_id, descriptor.capability_id)
        if key in self._providers:
            raise ValueError(f"provider contract already registered: {key}")
        if not callable(getattr(provider, "start", None)):
            raise ValueError("provider must implement start")
        self._providers[key] = provider

    def registered_catalog(self) -> tuple[CapabilityDescriptor, ...]:
        return tuple(
            self._providers[key].descriptor for key in sorted(self._providers)
        )

    def autonomous_catalog(self) -> tuple[CapabilityDescriptor, ...]:
        allowed_risks = {RiskLevel.READ_ONLY, RiskLevel.LOW_IMPACT}
        return tuple(
            descriptor
            for descriptor in self.registered_catalog()
            if (descriptor.provider_id, descriptor.capability_id)
            in self._autonomous_allowlist
            and descriptor.risk_level in allowed_risks
        )

    def start(self, task: TaskRun) -> ProviderEvent:
        provider = self._provider(task)
        self._validate_task_authority(task)
        try:
            validate_payload(
                task.input_payload,
                task.descriptor.input_schema,
                label="capability input",
            )
        except ValueError as exc:
            raise InvalidProviderEvent(str(exc)) from exc
        return self._validate_event(task, provider.start(task))

    def can_query(self, task: TaskRun) -> bool:
        provider = self._provider(task)
        return callable(getattr(provider, "query_status", None))

    def query_status(self, task: TaskRun) -> ProviderEvent | None:
        provider = self._provider(task)
        self._validate_task_authority(task)
        query = getattr(provider, "query_status", None)
        if not callable(query):
            return None
        event = query(task)
        if event is None:
            return None
        return self._validate_event(task, event)

    def cancel(self, task: TaskRun) -> ProviderEvent:
        provider = self._provider(task)
        self._validate_task_authority(task)
        cancel = getattr(provider, "cancel", None)
        if not callable(cancel):
            raise ProviderNotRegistered(
                f"provider does not support cancellation: {task.provider_id}"
            )
        return self._validate_event(task, cancel(task))

    def _provider(self, task: TaskRun) -> CapabilityProvider:
        key = (task.provider_id, task.capability_id)
        provider = self._providers.get(key)
        if provider is None:
            raise ProviderNotRegistered(
                f"provider is not registered: {task.provider_id}/{task.capability_id}"
            )
        if provider.descriptor != task.descriptor:
            raise ProviderNotRegistered("registered provider contract does not match task")
        return provider

    @staticmethod
    def _validate_task_authority(task: TaskRun) -> None:
        if task.status is not TaskStatus.RUNNING:
            raise InvalidProviderEvent("provider calls require a RUNNING task")
        try:
            normalize_descriptor(task.descriptor)
            normalize_request(task.request)
        except ValueError as exc:
            raise InvalidProviderEvent(str(exc)) from exc
        missing = sorted(
            set(task.descriptor.required_scopes) - set(task.authorization_scopes)
        )
        if missing:
            raise InvalidProviderEvent(
                f"task authorization is missing required scope: {missing[0]}"
            )
        if (
            task.descriptor.requires_confirmation
            and task.confirmed_by != task.requester_id
        ):
            raise InvalidProviderEvent(
                "high-risk task requires persisted requester confirmation"
            )

    @staticmethod
    def _validate_event(task: TaskRun, event: object) -> ProviderEvent:
        try:
            event = normalize_provider_event(event)
        except ValueError as exc:
            raise InvalidProviderEvent(str(exc)) from exc
        if event.task_id != task.task_id:
            raise InvalidProviderEvent("provider event belongs to a different task")
        if event.kind is ProviderEventKind.PROGRESS and not task.descriptor.supports_progress:
            raise InvalidProviderEvent("provider contract does not declare progress")
        if event.kind is ProviderEventKind.SUCCEEDED:
            assert event.result is not None
            try:
                validate_payload(
                    event.result,
                    task.descriptor.output_schema,
                    label="capability output",
                )
            except ValueError as exc:
                raise InvalidProviderEvent(str(exc)) from exc
        elif event.result is not None or event.media:
            raise InvalidProviderEvent("only succeeded events may carry result or media")
        allowed_media = frozenset(task.descriptor.media_output_kinds)
        for media in event.media:
            if media.kind not in allowed_media:
                raise InvalidProviderEvent(f"undeclared media kind: {media.kind}")
        return event


__all__ = (
    "AstrBotCapabilityAdapter",
    "CapabilityProvider",
    "InvalidProviderEvent",
    "ProviderNotRegistered",
)
