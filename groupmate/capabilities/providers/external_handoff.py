"""Built-in provider for explicit external or human handoff states."""

from __future__ import annotations

from ...models import StringEnum
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
from ..provider import CapabilityProvider


class ExternalHandoffReason(StringEnum):
    EXTERNAL_ACTION_REQUIRED = "external_action_required"
    HUMAN_REVIEW_REQUIRED = "human_review_required"


class ExternalHandoffTarget(StringEnum):
    CONFIGURED_SERVICE = "configured_service"
    HUMAN_OPERATOR = "human_operator"


class ExternalHandoffProvider(CapabilityProvider):
    name = "external_handoff"
    manifest = CapabilityManifest(
        name=name,
        version="1.0.0",
        supported_intents=("external_handoff",),
        permission_profile=(CapabilityPermission.EXTERNAL_HANDOFF,),
        latency_class=CapabilityLatencyClass.INLINE,
        cost_class=CapabilityCostClass.FREE,
        failure_policy=CapabilityFailurePolicy.HANDOFF,
        max_result_size=512,
        default_timeout_seconds=2.0,
        max_concurrency=1,
    )

    def __init__(
        self,
        reason: ExternalHandoffReason,
        target: ExternalHandoffTarget,
    ) -> None:
        if not isinstance(reason, ExternalHandoffReason):
            raise TypeError(
                "handoff reason must be an ExternalHandoffReason"
            )
        if not isinstance(target, ExternalHandoffTarget):
            raise TypeError(
                "handoff target must be an ExternalHandoffTarget"
            )
        self._reason = reason
        self._target = target
        super().__init__()

    async def execute(self, request: CapabilityRequest) -> CapabilityResult:
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

    async def __call__(
        self,
        request: CapabilityRequest,
    ) -> CapabilityResult:
        return await self.execute(request)
