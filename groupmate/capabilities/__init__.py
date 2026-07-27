"""Controlled capability contracts, registry, and built-in adapters."""

from .builtin import (
    ExternalHandoffCapability,
    VisionCapability,
    external_handoff_spec,
    vision_spec,
)
from .contracts import (
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    MediaCandidate,
)
from .registry import CapabilityRegistry, CapabilitySpec

__all__ = [
    "CapabilityRegistry",
    "CapabilityRequest",
    "CapabilityResult",
    "CapabilitySpec",
    "CapabilityStatus",
    "ExternalHandoffCapability",
    "MediaCandidate",
    "VisionCapability",
    "external_handoff_spec",
    "vision_spec",
]
