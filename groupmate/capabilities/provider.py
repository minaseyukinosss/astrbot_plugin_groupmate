"""Static provider contract for Groupmate-owned capabilities."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Sequence

from .contracts import (
    CapabilityManifest,
    CapabilityRequest,
    CapabilityResult,
)
from .registry import CapabilitySpec


@dataclass(frozen=True)
class CapabilityHealth:
    available: bool
    reason_code: str = "ready"
    checked_at: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.available, bool):
            raise TypeError("available must be a bool")
        reason = str(self.reason_code or "").strip()
        if not reason:
            raise ValueError("reason_code is required")
        object.__setattr__(self, "reason_code", reason)
        object.__setattr__(self, "checked_at", int(self.checked_at))


class CapabilityProvider(ABC):
    """Lifecycle and execution contract for a statically assembled provider."""

    manifest = None

    def __init__(self) -> None:
        if not isinstance(self.manifest, CapabilityManifest):
            raise TypeError("provider manifest is required")

    def start(self) -> None:
        return None

    def health(self) -> CapabilityHealth:
        return CapabilityHealth(True, "ready", 0)

    def required_information(
        self,
        request: CapabilityRequest,
    ) -> Sequence[str]:
        if not isinstance(request, CapabilityRequest):
            raise TypeError("request must be a CapabilityRequest")
        return ()

    @abstractmethod
    async def execute(self, request: CapabilityRequest) -> CapabilityResult:
        raise NotImplementedError

    def close(self) -> None:
        return None


def provider_spec(provider: CapabilityProvider) -> CapabilitySpec:
    """Build a compatibility spec without taking ownership of lifecycle."""
    if not isinstance(provider, CapabilityProvider):
        raise TypeError("provider must be a CapabilityProvider")
    health = provider.health()
    if not isinstance(health, CapabilityHealth):
        raise TypeError("provider health must be a CapabilityHealth")
    return CapabilitySpec(
        provider.manifest,
        provider.execute,
        required_information=provider.required_information,
        available=health.available,
    )
