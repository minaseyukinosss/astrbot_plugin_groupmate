from __future__ import annotations

import asyncio

from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.settings import SocialRuntimeSettings
from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.profile.repository import ProfileRepository
from groupmate.social_runtime.profile.service import ProfileService
from tests.factories import social_event_values


class _Closable:
    model = "test"

    def __init__(self):
        self.closed = 0

    async def close(self):
        self.closed += 1


def _settings(*, profile_enabled=True):
    return SocialRuntimeSettings.from_mapping(
        {
            "enabled_groups": ["g1"],
            "runtime_mode": "SHADOW",
            "generation_provider": "provider:text",
            "cognition_api_key": "sk-test",
            "profile_enabled": profile_enabled,
        }
    )


def test_bridge_composes_and_closes_one_member_style_worker(tmp_path):
    cognition = _Closable()
    profile = _Closable()
    member_style = _Closable()
    bridge = AstrBotSocialRuntimeBridge(
        object(),
        _settings(),
        tmp_path,
        cognition_client_factory=lambda _: cognition,
        profile_client_factory=lambda _: profile,
        member_style_client_factory=lambda _: member_style,
    )

    async def scenario():
        await bridge.start()
        service = bridge.member_style_service
        assert bridge.profile_service.style_service is service
        assert service.task_running is True
        await bridge.close()
        return service.task_running

    running_after_close = asyncio.run(scenario())

    assert running_after_close is False
    assert member_style.closed == 1
    assert profile.closed == 1
    assert cognition.closed == 1


def test_profile_disabled_does_not_start_style_distillation_worker(tmp_path):
    cognition = _Closable()
    member_style_calls = []
    bridge = AstrBotSocialRuntimeBridge(
        object(),
        _settings(profile_enabled=False),
        tmp_path,
        cognition_client_factory=lambda _: cognition,
        member_style_client_factory=lambda settings: member_style_calls.append(settings),
    )

    async def scenario():
        await bridge.start()
        status = bridge.runtime_status("g1")["member_style_status"]
        await bridge.close()
        return status

    status = asyncio.run(scenario())

    assert member_style_calls == []
    assert status == {
        "enabled": False,
        "task_running": False,
        "active_session": False,
        "session_expires_at": None,
        "last_diagnostic": None,
    }


def test_style_client_failure_does_not_block_social_runtime_start(tmp_path):
    cognition = _Closable()
    profile = _Closable()

    def unavailable(_settings):
        raise RuntimeError("secret provider detail")

    bridge = AstrBotSocialRuntimeBridge(
        object(),
        _settings(),
        tmp_path,
        cognition_client_factory=lambda _: cognition,
        profile_client_factory=lambda _: profile,
        member_style_client_factory=unavailable,
    )

    async def scenario():
        await bridge.start()
        status = bridge.runtime_status("g1")
        manager_started = bridge.manager is not None
        await bridge.close()
        return manager_started, status

    manager_started, status = asyncio.run(scenario())

    assert manager_started is True
    assert status["runtime_ready"] is True
    assert status["member_style_status"]["task_running"] is False
    assert status["member_style_status"]["last_diagnostic"] == (
        "style_worker_unavailable"
    )
    assert "secret provider detail" not in repr(status)


def test_profile_observation_only_wakes_style_worker_without_calling_model(tmp_path):
    class _StyleWorker:
        def __init__(self):
            self.wakes = 0

        def wake(self):
            self.wakes += 1

    worker = _StyleWorker()
    service = ProfileService(
        repository=ProfileRepository(tmp_path / "runtime.db"),
        extractor=None,
        persona_id="aemeath",
        group_ids=("g1",),
        style_service=worker,
    )
    event = SocialEventEnvelope.create(
        **social_event_values(
            event_id="qq:wake-1",
            persona_id="aemeath",
            group_id="g1",
            actor_id="u1",
        )
    )

    inserted = asyncio.run(service.observe(event))

    assert inserted is True
    assert worker.wakes == 1
