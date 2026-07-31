import asyncio

import pytest

from groupmate.capabilities import (
    CapabilityHealth,
    CapabilityManifest,
    CapabilityPermission,
    CapabilityProvider,
    CapabilityProviderRuntime,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
)
from groupmate.core.response_act import TaskResolutionStatus


def _manifest(name):
    return CapabilityManifest(
        name=name,
        version="1.0.0",
        permission_profile=(CapabilityPermission.VISION_READ,),
    )


class RecordingProvider(CapabilityProvider):
    def __init__(
        self,
        name,
        events,
        *,
        available=True,
        start_error=False,
        close_error=False,
    ):
        self.manifest = _manifest(name)
        self.events = events
        self.available = available
        self.start_error = start_error
        self.close_error = close_error
        self.calls = 0
        super().__init__()

    def start(self):
        self.events.append("start:" + self.manifest.name)
        if self.start_error:
            raise RuntimeError("start failed")

    def health(self):
        return CapabilityHealth(
            self.available,
            "ready" if self.available else "offline",
            100,
        )

    def required_information(self, request):
        super().required_information(request)
        return ("message_text",) if not request.message_text else ()

    async def execute(self, request):
        self.calls += 1
        return CapabilityResult(
            CapabilityStatus.SUCCESS,
            request.capability_name,
            facts=("ok",),
        )

    def close(self):
        self.events.append("close:" + self.manifest.name)
        if self.close_error:
            raise RuntimeError("close failed")


def test_runtime_starts_registers_and_closes_in_reverse_order():
    events = []
    runtime = CapabilityProviderRuntime(
        (
            RecordingProvider("one", events),
            RecordingProvider("two", events),
        )
    )

    assert tuple(item.name for item in runtime.registry.manifests()) == (
        "one",
        "two",
    )
    assert runtime.closed is False

    runtime.close()
    runtime.close()

    assert runtime.closed is True
    assert events == [
        "start:one",
        "start:two",
        "close:two",
        "close:one",
    ]


def test_runtime_preserves_provider_required_information():
    provider = RecordingProvider("echo", [])
    runtime = CapabilityProviderRuntime((provider,))

    resolution = runtime.registry.resolve(CapabilityRequest("echo"))

    assert resolution.status is TaskResolutionStatus.SUPPORTED
    assert resolution.required_information == ("message_text",)


def test_unhealthy_provider_is_registered_but_not_executed():
    provider = RecordingProvider("offline", [], available=False)
    runtime = CapabilityProviderRuntime((provider,))

    result = asyncio.run(
        runtime.registry.execute(CapabilityRequest("offline"))
    )

    assert result.status is CapabilityStatus.UNSUPPORTED
    assert result.error_code == "capability_unavailable"
    assert runtime.health("offline") == CapabilityHealth(
        False,
        "offline",
        100,
    )
    assert provider.calls == 0


def test_duplicate_manifest_name_fails_before_any_provider_starts():
    events = []

    with pytest.raises(ValueError, match="duplicate provider"):
        CapabilityProviderRuntime(
            (
                RecordingProvider("same", events),
                RecordingProvider("same", events),
            )
        )

    assert events == []


def test_start_failure_is_registered_unavailable_without_close():
    events = []
    failing = RecordingProvider("failing", events, start_error=True)
    healthy = RecordingProvider("healthy", events)
    runtime = CapabilityProviderRuntime((failing, healthy))

    assert runtime.health("failing") == CapabilityHealth(
        False,
        "start_error",
        0,
    )
    result = asyncio.run(
        runtime.registry.execute(CapabilityRequest("failing"))
    )
    assert result.error_code == "capability_unavailable"

    runtime.close()

    assert events == [
        "start:failing",
        "start:healthy",
        "close:healthy",
    ]


def test_close_failure_does_not_block_remaining_providers():
    events = []
    runtime = CapabilityProviderRuntime(
        (
            RecordingProvider("one", events),
            RecordingProvider("two", events, close_error=True),
        )
    )

    runtime.close()

    assert events[-2:] == ["close:two", "close:one"]


def test_runtime_rejects_non_provider_before_start():
    with pytest.raises(TypeError, match="CapabilityProvider"):
        CapabilityProviderRuntime((object(),))
