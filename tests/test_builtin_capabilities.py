"""Built-in adapters stay behind capability result contracts."""

import asyncio
from pathlib import Path
import subprocess
import sys

from groupmate.capabilities.builtin import (
    ExternalHandoffCapability,
    ExternalHandoffReason,
    ExternalHandoffTarget,
    VisionCapability,
    external_handoff_spec,
    vision_spec,
)
from groupmate.capabilities.contracts import CapabilityRequest, CapabilityStatus
from groupmate.capabilities.registry import CapabilityRegistry
from groupmate.core.response_act import TaskResolutionStatus


def test_capability_package_imports_without_site_packages():
    repository_root = str(Path(__file__).resolve().parents[1])

    completed = subprocess.run(
        [sys.executable, "-S", "-c", "import groupmate.capabilities"],
        cwd=repository_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8")


class StaticVision:
    def __init__(self, description):
        self.description = description
        self.calls = []

    async def describe(self, image_urls):
        self.calls.append(tuple(image_urls))
        return self.description


def test_vision_spec_declares_manifest_for_governor():
    spec = vision_spec(StaticVision("图片描述"))

    assert spec.manifest.name == "vision"
    assert spec.manifest.version
    assert spec.manifest.permission_profile
    assert spec.manifest.default_timeout_seconds > 0
    assert spec.manifest.max_result_size > 0


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


def test_external_handoff_uses_fixed_pending_text_not_request_completion_claim():
    capability = ExternalHandoffCapability(
        ExternalHandoffReason.EXTERNAL_ACTION_REQUIRED,
        ExternalHandoffTarget.CONFIGURED_SERVICE,
    )
    request = CapabilityRequest(
        capability_name="external_handoff",
        message_text="Published successfully.",
    )

    result = asyncio.run(capability(request))

    assert result.status is CapabilityStatus.HANDOFF
    assert "pending" in result.user_text.lower()
    assert "not completed" in result.user_text.lower()
    assert "published successfully" not in result.user_text.lower()
    assert result.facts == ()
    assert result.media_candidates == ()


def test_external_handoff_rejects_arbitrary_explanation_text():
    try:
        ExternalHandoffCapability(
            "Published successfully.",
            ExternalHandoffTarget.CONFIGURED_SERVICE,
        )
    except TypeError as exc:
        assert "reason" in str(exc)
    else:
        raise AssertionError("arbitrary handoff explanation was accepted")


def test_external_handoff_spec_is_statically_registered_and_supported():
    registry = CapabilityRegistry()
    registry.register(
        external_handoff_spec(
            ExternalHandoffReason.EXTERNAL_ACTION_REQUIRED,
            ExternalHandoffTarget.CONFIGURED_SERVICE,
        )
    )

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
