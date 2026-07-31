"""Built-in vision provider backed by the existing vision port."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional, Sequence

from ..contracts import (
    CapabilityCostClass,
    CapabilityFailurePolicy,
    CapabilityLatencyClass,
    CapabilityManifest,
    CapabilityPermission,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
)
from ..provider import CapabilityHealth, CapabilityProvider

if TYPE_CHECKING:
    from ...ports import VisionPort


class VisionProvider(CapabilityProvider):
    name = "vision"
    manifest = CapabilityManifest(
        name=name,
        version="1.0.0",
        supported_intents=("image_understanding",),
        permission_profile=(CapabilityPermission.VISION_READ,),
        latency_class=CapabilityLatencyClass.INTERACTIVE,
        cost_class=CapabilityCostClass.METERED,
        failure_policy=CapabilityFailurePolicy.FAIL_CLOSED,
        max_result_size=2048,
        default_timeout_seconds=10.0,
        max_concurrency=1,
    )

    def __init__(self, vision: Optional["VisionPort"]) -> None:
        self._vision = vision
        super().__init__()

    def health(self) -> CapabilityHealth:
        if self._vision is None:
            return CapabilityHealth(False, "vision_unavailable", 0)
        return CapabilityHealth(True, "ready", 0)

    def required_information(
        self,
        request: CapabilityRequest,
    ) -> Sequence[str]:
        super().required_information(request)
        return () if request.media_locators else ("media_locator",)

    async def execute(self, request: CapabilityRequest) -> CapabilityResult:
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
            raw_description = await self._vision.describe(
                request.media_locators
            )
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

    async def __call__(
        self,
        request: CapabilityRequest,
    ) -> CapabilityResult:
        return await self.execute(request)
