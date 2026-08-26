from __future__ import annotations

import asyncio
import time

from groupmate.adapters.deepseek_profile import ProfileModelError, ProfileModelResponse
from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.settings import SocialRuntimeSettings
from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.profile.extractor import ProfileExtractor
from groupmate.social_runtime.profile.policy import ProfileEvidencePolicy
from groupmate.social_runtime.profile.repository import ProfileRepository
from groupmate.social_runtime.profile.service import ProfileService


class _Client:
    model = "profile-test"

    def __init__(self, payload=None, error=None):
        self.payload = payload or {"facts": [], "episodes": [], "edges": []}
        self.error = error
        self.calls = 0

    async def extract(self, batch):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return ProfileModelResponse(
            payload=self.payload,
            latency_ms=1,
            request_bytes=1,
            backend="test",
            model=self.model,
        )


def _event(event_id="event-1"):
    return SocialEventEnvelope.create(
        event_id=event_id,
        event_type="platform.message",
        occurred_at=100,
        received_at=100,
        persona_id="persona",
        group_id="group-1",
        actor_id="member-1",
        source_message_id=event_id,
        correlation_id=event_id,
        causation_id=None,
        payload={
            "platform": "qq",
            "text": "我喜欢冷饮",
            "sender": {"id": "member-1", "name": "群友甲"},
            "mentions": [],
            "reply_to_actor_id": None,
        },
    )


def _service(tmp_path, client):
    repository = ProfileRepository(tmp_path / "groupmate-social-runtime-v2.db")
    return ProfileService(
        repository=repository,
        extractor=ProfileExtractor(client, ProfileEvidencePolicy()),
        persona_id="persona",
        group_ids=("group-1",),
        batch_size=1,
        interval_seconds=600,
        clock=lambda: 100,
    )


def test_observe_durably_queues_without_calling_profile_model(tmp_path):
    client = _Client()
    service = _service(tmp_path, client)

    started = time.monotonic()
    asyncio.run(service.observe(_event()))

    assert time.monotonic() - started < 0.1
    assert service.pending_count("group-1") == 1
    assert client.calls == 0


def test_due_batch_persists_valid_fact(tmp_path):
    client = _Client(
        {
            "facts": [
                {
                    "subject_id": "member-1",
                    "category": "preference",
                    "summary": "喜欢冷饮",
                    "source_kind": "self_statement",
                    "source_actor_id": "member-1",
                    "evidence_event_ids": ["event-1"],
                    "confidence": 0.93,
                }
            ],
            "episodes": [],
            "edges": [],
        }
    )
    service = _service(tmp_path, client)
    asyncio.run(service.observe(_event()))

    asyncio.run(service.process_due(now=100))

    assert [
        item.summary
        for item in service.repository.facts(
            "persona", "group-1", "member-1", injectable_only=True
        )
    ] == ["喜欢冷饮"]
    assert service.pending_count("group-1") == 0
    snapshot = service.repository.snapshot("persona", "group-1", "member-1")
    assert snapshot is not None
    assert snapshot.one_line_portrait == "喜欢冷饮"


def test_provider_failure_keeps_observation_for_safe_retry(tmp_path):
    service = _service(
        tmp_path,
        _Client(error=ProfileModelError("profile_network_failed")),
    )
    asyncio.run(service.observe(_event()))

    asyncio.run(service.process_due(now=100))

    assert service.pending_count("group-1") == 1
    assert service.diagnostics("group-1") == ("profile_network_failed",)


def test_bridge_lifecycle_queues_profile_work_without_calling_provider(tmp_path):
    class _ClosableClient(_Client):
        def __init__(self):
            super().__init__()
            self.closed = 0

        async def close(self):
            self.closed += 1

    class _RawEvent:
        class MessageObject:
            self_id = "bot-1"
            raw_message = {
                "message_id": "profile-bridge-1",
                "group_id": "group-1",
                "user_id": "member-1",
                "time": 100,
                "message": [{"type": "text", "data": {"text": "你好"}}],
                "sender": {"nickname": "群友甲"},
            }

        message_obj = MessageObject()
        message_str = "你好"
        unified_msg_origin = "aiocqhttp:GroupMessage:group-1"

        @staticmethod
        def get_platform_id():
            return "onebot-main"

    cognition = _ClosableClient()
    profile = _ClosableClient()
    settings = SocialRuntimeSettings.from_mapping(
        {
            "enabled_groups": ["group-1"],
            "runtime_mode": "SHADOW",
            "generation_provider": "provider:text",
            "cognition_api_key": "sk-test",
        }
    )
    bridge = AstrBotSocialRuntimeBridge(
        object(),
        settings,
        tmp_path,
        clock=lambda: 100,
        cognition_client_factory=lambda _settings: cognition,
        profile_client_factory=lambda _settings: profile,
    )

    async def scenario():
        await bridge.start()
        await bridge.observe_event(_RawEvent())
        pending = bridge.profile_service.pending_count("group-1")
        await bridge.close()
        return pending

    pending = asyncio.run(scenario())

    assert pending == 1
    assert profile.calls == 0
    assert profile.closed == cognition.closed == 1
