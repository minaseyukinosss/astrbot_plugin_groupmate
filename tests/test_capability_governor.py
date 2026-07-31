import asyncio

import pytest

from groupmate.capabilities import (
    CapabilityContext,
    CapabilityGovernor,
    CapabilityManifest,
    CapabilityMediaPolicy,
    CapabilityPermission,
    CapabilityRegistry,
    CapabilityRequest,
    CapabilityResult,
    CapabilitySpec,
    CapabilityStatus,
    MediaCandidate,
)


def _manifest(name="echo", **overrides):
    values = {
        "name": name,
        "version": "1.0.0",
        "permission_profile": (CapabilityPermission.VISION_READ,),
        "default_timeout_seconds": 0.1,
        "max_result_size": 256,
        "max_concurrency": 1,
    }
    values.update(overrides)
    return CapabilityManifest(**values)


def _context(**overrides):
    values = {
        "persona_id": "aemeath",
        "group_id": "g1",
        "actor_id": "u1",
        "message_id": "m1",
        "trace_id": "d1",
        "deadline_at": 200,
        "allowed_permissions": (CapabilityPermission.VISION_READ,),
        "media_policy": CapabilityMediaPolicy(
            capability_media_allowed=True,
            allowed_media_kinds=("image",),
            allowed_safety_labels=("safe",),
        ),
    }
    values.update(overrides)
    return CapabilityContext(**values)


def _request(name="echo", **overrides):
    values = {
        "capability_name": name,
        "message_text": "hello",
        "group_id": "g1",
        "actor_id": "u1",
        "message_id": "m1",
    }
    values.update(overrides)
    return CapabilityRequest(**values)


async def _echo(request):
    return CapabilityResult(
        CapabilityStatus.SUCCESS,
        request.capability_name,
        facts=(request.message_text,),
        user_text=request.message_text,
    )


def test_unregistered_capability_is_unsupported_without_executor():
    registry = CapabilityRegistry()
    governor = CapabilityGovernor(registry)

    result = asyncio.run(governor.execute(_request("missing"), _context(), now=100))

    assert result.status is CapabilityStatus.UNSUPPORTED
    assert result.error_code == "capability_not_registered"


def test_missing_permission_denies_before_executor_runs():
    calls = {"count": 0}

    async def executor(request):
        calls["count"] += 1
        return await _echo(request)

    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(_manifest("echo"), executor))
    governor = CapabilityGovernor(registry)

    result = asyncio.run(
        governor.execute(
            _request("echo"),
            _context(allowed_permissions=()),
            now=100,
        )
    )

    assert result.status is CapabilityStatus.UNSUPPORTED
    assert result.error_code == "permission_denied"
    assert calls["count"] == 0


def test_deadline_expired_denies_before_executor_runs():
    calls = {"count": 0}

    async def executor(request):
        calls["count"] += 1
        return await _echo(request)

    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(_manifest("echo"), executor))
    governor = CapabilityGovernor(registry)

    result = asyncio.run(
        governor.execute(_request("echo"), _context(deadline_at=99), now=100)
    )

    assert result.status is CapabilityStatus.TIMEOUT
    assert result.error_code == "deadline_expired"
    assert calls["count"] == 0


def test_manifest_timeout_is_passed_to_registry():
    async def slow(_request):
        await asyncio.sleep(1)
        return CapabilityResult(CapabilityStatus.SUCCESS, "slow", facts=("done",))

    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(
            _manifest("slow", default_timeout_seconds=0.001),
            slow,
        )
    )
    governor = CapabilityGovernor(registry)

    result = asyncio.run(governor.execute(_request("slow"), _context(), now=100))

    assert result.status is CapabilityStatus.TIMEOUT
    assert result.error_code == "execution_timeout"


def test_media_policy_strips_disallowed_media_but_keeps_facts():
    candidate = MediaCandidate(
        media_id="img-1",
        source="provider",
        locator="https://example.test/1.png",
        media_kind="image",
        semantic_label="preview",
        purpose="reply attachment",
        safety_label="safe",
    )

    async def executor(request):
        return CapabilityResult(
            CapabilityStatus.SUCCESS,
            request.capability_name,
            facts=("fact",),
            user_text="fact",
            media_candidates=(candidate,),
        )

    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(_manifest("echo"), executor))
    governor = CapabilityGovernor(registry)

    result = asyncio.run(
        governor.execute(
            _request("echo"),
            _context(
                media_policy=CapabilityMediaPolicy(
                    capability_media_allowed=False
                )
            ),
            now=100,
        )
    )

    assert result.status is CapabilityStatus.SUCCESS
    assert result.facts == ("fact",)
    assert result.media_candidates == ()


def test_result_size_limit_fails_closed():
    async def executor(request):
        return CapabilityResult(
            CapabilityStatus.SUCCESS,
            request.capability_name,
            facts=("x" * 20,),
            user_text="x" * 20,
        )

    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(_manifest("echo", max_result_size=10), executor))
    governor = CapabilityGovernor(registry)

    result = asyncio.run(governor.execute(_request("echo"), _context(), now=100))

    assert result.status is CapabilityStatus.FAILED
    assert result.error_code == "result_too_large"


def test_external_cancellation_is_not_swallowed():
    async def executor(_request):
        raise asyncio.CancelledError()

    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(_manifest("cancelled"), executor))
    governor = CapabilityGovernor(registry)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(governor.execute(_request("cancelled"), _context(), now=100))
