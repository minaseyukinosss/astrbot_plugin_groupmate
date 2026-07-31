"""Controlled capability contracts, registry, and built-in adapters."""

from .builtin import (
    ExternalHandoffCapability,
    ExternalHandoffReason,
    ExternalHandoffTarget,
    VisionCapability,
    external_handoff_spec,
    vision_spec,
)
from .contracts import (
    CapabilityContext,
    CapabilityCostClass,
    CapabilityFailurePolicy,
    CapabilityLatencyClass,
    CapabilityManifest,
    CapabilityMediaPolicy,
    CapabilityPermission,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    MediaCandidate,
)
from .governor import CapabilityGovernor
from .provider import CapabilityHealth, CapabilityProvider
from .provider_runtime import CapabilityProviderRuntime
from .registry import CapabilityRegistry, CapabilitySpec

__all__ = [
    "CapabilityRegistry",
    "CapabilityContext",
    "CapabilityCostClass",
    "CapabilityFailurePolicy",
    "CapabilityGovernor",
    "CapabilityHealth",
    "CapabilityLatencyClass",
    "CapabilityManifest",
    "CapabilityMediaPolicy",
    "CapabilityPermission",
    "CapabilityProvider",
    "CapabilityProviderRuntime",
    "CapabilityRequest",
    "CapabilityResult",
    "CapabilitySpec",
    "CapabilityStatus",
    "ExternalHandoffCapability",
    "ExternalHandoffReason",
    "ExternalHandoffTarget",
    "MediaCandidate",
    "VisionCapability",
    "external_handoff_spec",
    "vision_spec",
]
