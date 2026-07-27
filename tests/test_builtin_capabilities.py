"""Built-in adapters stay behind capability result contracts."""

import asyncio

from groupmate.capabilities.builtin import (
    ExternalHandoffCapability,
    VisionCapability,
    external_handoff_spec,
    vision_spec,
)
from groupmate.capabilities.contracts import CapabilityRequest, CapabilityStatus
from groupmate.capabilities.registry import CapabilityRegistry
from groupmate.core.response_act import TaskResolutionStatus


class StaticVision:
    def __init__(self, description):
        self.description = description
        self.calls = []

    async def describe(self, image_urls):
        self.calls.append(tuple(image_urls))
        return self.description


def _vision_request(*locators):
    return CapabilityRequest(
        capability_name="vision",
        message_text="describe this",
        media_locators=locators,
        group_id="group-1",
        actor_id="user-1",
        message_id="message-1",
    )


def test_vision_adapter_returns_description_as_success_fact():
    vision = StaticVision("  a white cat beside a keyboard  ")
    registry = CapabilityRegistry()
    registry.register(vision_spec(vision))

    result = asyncio.run(
        registry.execute(_vision_request("https://example.test/cat.png"))
    )

    assert result.status is CapabilityStatus.SUCCESS
    assert result.facts == ("a white cat beside a keyboard",)
    assert result.user_text == "a white cat beside a keyboard"
    assert vision.calls == [("https://example.test/cat.png",)]


def test_vision_without_port_is_explicitly_unsupported():
    capability = VisionCapability(None)

    result = asyncio.run(capability(_vision_request("asset://image/1")))

    assert result.status is CapabilityStatus.UNSUPPORTED
    assert result.error_code == "vision_unavailable"
    assert result.facts == ()


def test_vision_spec_without_port_resolves_as_unsupported():
    registry = CapabilityRegistry()
    registry.register(vision_spec(None))

    resolution = registry.resolve(_vision_request("asset://image/1"))

    assert resolution.status is TaskResolutionStatus.UNSUPPORTED
    assert resolution.capability_name == "vision"


def test_vision_missing_media_is_declared_and_fails_closed_if_executed():
    vision = StaticVision("unused")
    registry = CapabilityRegistry()
    registry.register(vision_spec(vision))
    request = _vision_request()

    resolution = registry.resolve(request)
    result = asyncio.run(registry.execute(request))

    assert resolution.required_information == ("media_locator",)
    assert result.status is CapabilityStatus.FAILED
    assert result.error_code == "missing_media"
    assert vision.calls == []


def test_vision_empty_description_is_failure_not_success():
    registry = CapabilityRegistry()
    registry.register(vision_spec(StaticVision(None)))

    result = asyncio.run(registry.execute(_vision_request("asset://image/1")))

    assert result.status is CapabilityStatus.FAILED
    assert result.error_code == "empty_result"
    assert result.facts == ()


def test_vision_exception_is_contained_by_adapter():
    class ExplodingVision:
        async def describe(self, _image_urls):
            raise OSError("provider unavailable")

    capability = VisionCapability(ExplodingVision())

    result = asyncio.run(capability(_vision_request("asset://image/1")))

    assert result.status is CapabilityStatus.FAILED
    assert result.error_code == "vision_error"
    assert result.diagnostic == "OSError"


def test_external_handoff_only_describes_handoff_without_claiming_completion():
    capability = ExternalHandoffCapability(
        "This request requires the configured external service."
    )
    request = CapabilityRequest(
        capability_name="external_handoff",
        message_text="publish the report",
    )

    result = asyncio.run(capability(request))

    assert result.status is CapabilityStatus.HANDOFF
    assert result.user_text == (
        "This request requires the configured external service."
    )
    assert result.facts == ()
    assert result.media_candidates == ()


def test_external_handoff_spec_is_statically_registered_and_supported():
    registry = CapabilityRegistry()
    registry.register(external_handoff_spec("External action required."))

    resolution = registry.describe("external_handoff")
    result = asyncio.run(
        registry.execute(
            CapabilityRequest(
                capability_name="external_handoff",
                message_text="do it",
            )
        )
    )

    assert resolution.status is TaskResolutionStatus.SUPPORTED
    assert result.status is CapabilityStatus.HANDOFF
    assert result.error_code == "external_action_required"
