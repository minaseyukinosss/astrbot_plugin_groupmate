"""Built-in adapters for existing ports and explicit external handoffs."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

from ..models import StringEnum
from .contracts import CapabilityRequest, CapabilityResult, CapabilityStatus
from .registry import CapabilitySpec

if TYPE_CHECKING:
    from ..ports import VisionPort


class ExternalHandoffReason(StringEnum):
    EXTERNAL_ACTION_REQUIRED = "external_action_required"
    HUMAN_REVIEW_REQUIRED = "human_review_required"


class ExternalHandoffTarget(StringEnum):
    CONFIGURED_SERVICE = "configured_service"
    HUMAN_OPERATOR = "human_operator"


class VisionCapability:
    name = "vision"

    def __init__(self, vision: Optional["VisionPort"]) -> None:
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

    def __init__(
        self,
        reason: ExternalHandoffReason,
        target: ExternalHandoffTarget,
    ) -> None:
        if not isinstance(reason, ExternalHandoffReason):
            raise TypeError("handoff reason must be an ExternalHandoffReason")
        if not isinstance(target, ExternalHandoffTarget):
            raise TypeError("handoff target must be an ExternalHandoffTarget")
        self._reason = reason
        self._target = target

    async def __call__(self, request: CapabilityRequest) -> CapabilityResult:
        del request
        if self._target is ExternalHandoffTarget.HUMAN_OPERATOR:
            target_text = "a human operator"
        else:
            target_text = "the configured external service"
        return CapabilityResult(
            CapabilityStatus.HANDOFF,
            self.name,
            user_text=(
                "This request is pending and not completed. "
                "It requires handoff to {}.".format(target_text)
            ),
            error_code=self._reason.value,
        )


def vision_spec(vision: Optional["VisionPort"]) -> CapabilitySpec:
    capability = VisionCapability(vision)
    return CapabilitySpec(
        capability.name,
        capability,
        required_information=lambda request: (
            () if request.media_locators else ("media_locator",)
        ),
        available=vision is not None,
    )


def external_handoff_spec(
    reason: ExternalHandoffReason,
    target: ExternalHandoffTarget,
) -> CapabilitySpec:
    capability = ExternalHandoffCapability(reason, target)
    return CapabilitySpec(capability.name, capability)
