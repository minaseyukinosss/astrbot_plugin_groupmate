"""Framework-independent contracts for host event adapters."""

from __future__ import annotations

from abc import ABC, ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from ...models import ChatMessage, MessageOrigin, StringEnum


@dataclass(frozen=True)
class HostEventAdapterManifest:
    name: str
    event_kinds: Tuple[str, ...]

    def __post_init__(self) -> None:
        name = str(self.name or "").strip().lower()
        if not name:
            raise ValueError("adapter manifest requires a name")

        if isinstance(self.event_kinds, str):
            raise ValueError("adapter event kinds must be a tuple")
        raw_kinds = tuple(self.event_kinds or ())
        kinds = []
        seen_kinds = set()
        for value in raw_kinds:
            kind = str(value or "").strip().lower()
            if not kind:
                raise ValueError("adapter event kinds cannot be empty")
            if kind in seen_kinds:
                continue
            seen_kinds.add(kind)
            kinds.append(kind)
        if not kinds:
            raise ValueError("adapter manifest requires event kinds")

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "event_kinds", tuple(kinds))


class HostEventAdapterStatus(StringEnum):
    NOT_MATCHED = "not_matched"
    BYPASSED = "bypassed"
    ADMITTED = "admitted"


@dataclass(frozen=True)
class HostEventAdapterResult:
    status: HostEventAdapterStatus
    reason_code: str
    message: Optional[ChatMessage] = None

    def __post_init__(self) -> None:
        status, reason_code = self._validate_shape(
            self.status,
            self.reason_code,
            self.message,
        )
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "reason_code", reason_code)

    @classmethod
    def validate(cls, result: "HostEventAdapterResult") -> None:
        if not isinstance(result, cls):
            raise TypeError("adapter result must use the contract type")
        cls._validate_shape(result.status, result.reason_code, result.message)

    @classmethod
    def _validate_shape(
        cls,
        status: Any,
        reason_code: Any,
        message: Optional[ChatMessage],
    ) -> Tuple[HostEventAdapterStatus, str]:
        if not isinstance(status, HostEventAdapterStatus):
            status = HostEventAdapterStatus(str(status).strip().lower())
        reason_code = str(reason_code or "").strip().lower()
        if not reason_code:
            raise ValueError("adapter result requires a reason code")

        if status is HostEventAdapterStatus.ADMITTED:
            cls._validate_admitted_message(message)
        elif message is not None:
            raise ValueError("non-admitted adapter result cannot carry a message")

        return status, reason_code

    @staticmethod
    def _validate_admitted_message(message: Optional[ChatMessage]) -> None:
        if not isinstance(message, ChatMessage):
            raise ValueError("admitted adapter result requires a ChatMessage")
        if message.origin is not MessageOrigin.SYSTEM_SYNTHETIC:
            raise ValueError("admitted message must be system synthetic")

        metadata = message.metadata
        if not isinstance(metadata, dict):
            raise ValueError("admitted message metadata must be a dictionary")
        allowed_keys = {
            "interaction_kind",
            "source_adapter",
            "target_id",
        }
        if set(metadata) - allowed_keys or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            for key, value in metadata.items()
        ):
            raise ValueError("admitted message metadata violates the contract")

    @classmethod
    def not_matched(cls) -> "HostEventAdapterResult":
        return cls(HostEventAdapterStatus.NOT_MATCHED, "not_matched")

    @classmethod
    def bypassed(cls, reason_code: str) -> "HostEventAdapterResult":
        return cls(HostEventAdapterStatus.BYPASSED, reason_code)

    @classmethod
    def admitted(cls, message: ChatMessage) -> "HostEventAdapterResult":
        return cls(HostEventAdapterStatus.ADMITTED, "admitted", message)


class _HostEventAdapterMeta(ABCMeta):
    def __call__(cls, *args: Any, **kwargs: Any) -> "HostEventAdapter":
        adapter = super().__call__(*args, **kwargs)
        if not isinstance(adapter.manifest, HostEventAdapterManifest):
            raise TypeError("adapter requires a valid manifest")
        return adapter


class HostEventAdapter(ABC, metaclass=_HostEventAdapterMeta):
    manifest = None

    def __init__(self) -> None:
        if not isinstance(
            self.manifest,
            HostEventAdapterManifest,
        ):
            raise TypeError("adapter requires a valid manifest")

    @abstractmethod
    def adapt(self, event: Any) -> HostEventAdapterResult:
        """Translate a claimed host event into a framework-independent result."""
