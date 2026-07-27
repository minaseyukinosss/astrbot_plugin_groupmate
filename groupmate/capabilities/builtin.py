"""Built-in adapters for existing ports and explicit external handoffs."""

from __future__ import annotations

import asyncio
from typing import Optional

from ..ports import VisionPort
from .contracts import CapabilityRequest, CapabilityResult, CapabilityStatus
from .registry import CapabilitySpec


class VisionCapability:
    name = "vision"

    def __init__(self, vision: Optional[VisionPort]) -> None:
        self._vision = vision

    async def __call__(self, request: CapabilityRequest) -> CapabilityResult:
        if self._vision is None:
            return CapabilityResult(
                CapabilityStatus.UNSUPPORTED,
                self.name,
                user_text="Vision is not available.",
                error_code="vision_unavailable",
            )
        if not request.media_locators:
            return CapabilityResult(
                CapabilityStatus.FAILED,
                self.name,
                user_text="An image is required for vision.",
                error_code="missing_media",
            )
        try:
            raw_description = await self._vision.describe(request.media_locators)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - adapter fails closed
            return CapabilityResult(
                CapabilityStatus.FAILED,
                self.name,
                user_text="Vision could not describe the image.",
                error_code="vision_error",
                diagnostic=type(exc).__name__,
            )
        description = " ".join(str(raw_description or "").split())
        if not description:
            return CapabilityResult(
                CapabilityStatus.FAILED,
                self.name,
                user_text="Vision returned no description.",
                error_code="empty_result",
            )
        return CapabilityResult(
            CapabilityStatus.SUCCESS,
            self.name,
            facts=(description,),
            user_text=description,
        )


class ExternalHandoffCapability:
    name = "external_handoff"

    def __init__(self, explanation: str) -> None:
        self._explanation = " ".join(str(explanation or "").split())
        if not self._explanation:
            raise ValueError("handoff explanation is required")

    async def __call__(self, request: CapabilityRequest) -> CapabilityResult:
        del request
        return CapabilityResult(
            CapabilityStatus.HANDOFF,
            self.name,
            user_text=self._explanation,
            error_code="external_action_required",
        )


def vision_spec(vision: Optional[VisionPort]) -> CapabilitySpec:
    capability = VisionCapability(vision)
    return CapabilitySpec(
        capability.name,
        capability,
        required_information=lambda request: (
            () if request.media_locators else ("media_locator",)
        ),
        available=vision is not None,
    )


def external_handoff_spec(explanation: str) -> CapabilitySpec:
    capability = ExternalHandoffCapability(explanation)
    return CapabilitySpec(capability.name, capability)
