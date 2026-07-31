"""Static registration and execution boundaries for capabilities."""

import asyncio

import pytest

from groupmate.capabilities.contracts import (
    CapabilityManifest,
    CapabilityPermission,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
)
from groupmate.capabilities.registry import CapabilityRegistry, CapabilitySpec
from groupmate.core.response_act import TaskResolutionStatus


def _request(name="echo", **overrides):
    values = {
        "capability_name": name,
        "message_text": "hello",
    }
    values.update(overrides)
    return CapabilityRequest(**values)


def _manifest(name="echo", **overrides):
    values = {
        "name": name,
        "version": "1.0.0",
        "permission_profile": (CapabilityPermission.VISION_READ,),
        "default_timeout_seconds": 0.1,
    }
    values.update(overrides)
    return CapabilityManifest(**values)


async def _echo(request):
    return CapabilityResult(
        CapabilityStatus.SUCCESS,
        request.capability_name,
        facts=(request.message_text,),
        user_text=request.message_text,
    )


def test_register_lookup_and_execute_use_only_explicit_names():
    registry = CapabilityRegistry(default_timeout_seconds=0.1)
    spec = CapabilitySpec(_manifest("echo"), _echo)

    registry.register(spec)
    result = asyncio.run(registry.execute(_request()))

    assert registry.lookup("echo") is spec
    assert registry.lookup("missing") is None
    assert result.status is CapabilityStatus.SUCCESS
    assert result.facts == ("hello",)


def test_unknown_name_returns_unsupported_without_dynamic_execution():
    registry = CapabilityRegistry()

    result = asyncio.run(registry.execute(_request("os.system")))

    assert result.status is CapabilityStatus.UNSUPPORTED
    assert result.capability_name == "os.system"
    assert result.facts == ()
    assert result.error_code == "capability_not_registered"


def test_duplicate_registration_is_rejected():
    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(_manifest("echo"), _echo))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(CapabilitySpec(_manifest("echo"), _echo))


@pytest.mark.parametrize(
    "factory",
    (
        lambda: CapabilitySpec(_manifest("Invalid Name"), _echo),
        lambda: CapabilitySpec(_manifest("echo"), None),
        lambda: CapabilitySpec(
            _manifest("echo"),
            _echo,
            required_information="not callable",
        ),
    ),
)
def test_invalid_capability_contract_is_rejected(factory):
    with pytest.raises((TypeError, ValueError)):
        factory()


def test_handler_exception_returns_failed_result():
    async def exploding(_request):
        raise RuntimeError("provider secret must not leak")

    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(_manifest("explode"), exploding))

    result = asyncio.run(registry.execute(_request("explode")))

    assert result.status is CapabilityStatus.FAILED
    assert result.error_code == "execution_error"
    assert result.diagnostic == "RuntimeError"
    assert "provider secret" not in result.diagnostic


def test_timeout_returns_explicit_timeout_result():
    async def slow(_request):
        await asyncio.sleep(1)
        return CapabilityResult(CapabilityStatus.SUCCESS, "slow", facts=("done",))

    registry = CapabilityRegistry(default_timeout_seconds=0.001)
    registry.register(CapabilitySpec(_manifest("slow"), slow))

    result = asyncio.run(registry.execute(_request("slow")))

    assert result.status is CapabilityStatus.TIMEOUT
    assert result.error_code == "execution_timeout"
    assert result.facts == ()


def test_external_cancellation_is_not_swallowed_as_failure():
    async def scenario():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def blocking(_request):
            started.set()
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()

        registry = CapabilityRegistry(default_timeout_seconds=30)
        registry.register(CapabilitySpec(_manifest("blocking"), blocking))
        task = asyncio.ensure_future(registry.execute(_request("blocking")))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await cancelled.wait()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "returned",
    (
        None,
        "success",
        CapabilityResult(CapabilityStatus.SUCCESS, "other", facts=("done",)),
    ),
)
def test_invalid_handler_result_fails_closed(returned):
    async def invalid(_request):
        return returned

    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(_manifest("invalid"), invalid))

    result = asyncio.run(registry.execute(_request("invalid")))

    assert result.status is CapabilityStatus.FAILED
    assert result.error_code == "invalid_result"
    assert result.facts == ()


def test_describe_and_resolve_map_to_task_resolution_contract():
    def missing_input(request):
        return () if request.message_text else ("message_text",)

    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(
            _manifest("echo"),
            _echo,
            required_information=missing_input,
        )
    )

    described = registry.describe("echo")
    missing = registry.describe("missing")
    resolved = registry.resolve(_request(message_text=""))

    assert described.status is TaskResolutionStatus.SUPPORTED
    assert described.capability_name == "echo"
    assert missing.status is TaskResolutionStatus.UNSUPPORTED
    assert missing.capability_name == "missing"
    assert resolved.status is TaskResolutionStatus.SUPPORTED
    assert resolved.capability_name == "echo"
    assert resolved.required_information == ("message_text",)


def test_resolve_does_not_swallow_matcher_cancellation_on_python_37():
    def cancelled(_request):
        raise asyncio.CancelledError()

    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(
            _manifest("cancelled"),
            _echo,
            required_information=cancelled,
        )
    )

    with pytest.raises(asyncio.CancelledError):
        registry.resolve(_request("cancelled"))


def test_unavailable_registered_capability_resolves_and_executes_as_unsupported():
    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(_manifest("offline"), _echo, available=False)
    )

    resolution = registry.resolve(_request("offline"))
    result = asyncio.run(registry.execute(_request("offline")))

    assert resolution.status is TaskResolutionStatus.UNSUPPORTED
    assert resolution.capability_name == "offline"
    assert result.status is CapabilityStatus.UNSUPPORTED
    assert result.error_code == "capability_unavailable"


def test_spec_owns_manifest_and_exposes_name_for_existing_callers():
    spec = CapabilitySpec(_manifest("echo"), _echo)

    assert spec.name == "echo"
    assert spec.manifest.name == "echo"
    assert spec.manifest.version == "1.0.0"


def test_registry_lists_registered_manifests_without_executors():
    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(_manifest("echo"), _echo))

    manifests = registry.manifests()

    assert tuple(item.name for item in manifests) == ("echo",)
    assert all(not hasattr(item, "executor") for item in manifests)


def test_duplicate_manifest_name_is_rejected():
    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(_manifest("echo"), _echo))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(
            CapabilitySpec(_manifest("echo", version="2.0.0"), _echo)
        )
