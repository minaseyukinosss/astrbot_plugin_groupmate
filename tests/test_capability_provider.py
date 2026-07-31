import asyncio
from dataclasses import FrozenInstanceError

import pytest

from groupmate.capabilities import (
    CapabilityHealth,
    CapabilityManifest,
    CapabilityPermission,
    CapabilityProvider,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
)


class EchoProvider(CapabilityProvider):
    manifest = CapabilityManifest(
        name="echo",
        version="1.0.0",
        permission_profile=(CapabilityPermission.VISION_READ,),
    )

    async def execute(self, request):
        return CapabilityResult(
            CapabilityStatus.SUCCESS,
            request.capability_name,
            facts=(request.message_text,),
        )


def test_health_is_immutable_and_validated():
    health = CapabilityHealth(True, "ready", 100)

    assert health.available is True
    assert health.reason_code == "ready"
    assert health.checked_at == 100
    with pytest.raises(FrozenInstanceError):
        health.available = False

    with pytest.raises(TypeError):
        CapabilityHealth("yes", "ready", 100)
    with pytest.raises(ValueError):
        CapabilityHealth(True, "", 100)


def test_provider_defaults_are_safe():
    provider = EchoProvider()
    provider.start()

    assert provider.health() == CapabilityHealth(True, "ready", 0)
    assert provider.required_information(CapabilityRequest("echo")) == ()
    result = asyncio.run(
        provider.execute(CapabilityRequest("echo", "hello"))
    )
    assert result.facts == ("hello",)

    provider.close()


def test_provider_requires_manifest():
    class MissingManifest(CapabilityProvider):
        async def execute(self, request):
            raise AssertionError(request)

    with pytest.raises(TypeError):
        MissingManifest()


def test_provider_requires_execute_implementation():
    class MissingExecute(CapabilityProvider):
        manifest = EchoProvider.manifest

    with pytest.raises(TypeError):
        MissingExecute()


def test_required_information_rejects_non_request():
    with pytest.raises(TypeError):
        EchoProvider().required_information(object())
