"""Compatibility factories for built-in capability providers."""

from __future__ import annotations

from typing import Optional

from .provider import provider_spec
from .providers import (
    ExternalHandoffProvider,
    ExternalHandoffReason,
    ExternalHandoffTarget,
    VisionProvider,
)
from .registry import CapabilitySpec

if False:  # pragma: no cover - type-only import for Python 3.7
    from ..ports import VisionPort


VisionCapability = VisionProvider
ExternalHandoffCapability = ExternalHandoffProvider


def vision_spec(vision: Optional["VisionPort"]) -> CapabilitySpec:
    return provider_spec(VisionProvider(vision))


def external_handoff_spec(
    reason: ExternalHandoffReason,
    target: ExternalHandoffTarget,
) -> CapabilitySpec:
    return provider_spec(ExternalHandoffProvider(reason, target))
