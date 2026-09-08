from __future__ import annotations

import asyncio
import time
from dataclasses import replace

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


class _SequenceClient(_Client):
    def __init__(self, payloads):
        super().__init__()
        self.payloads = list(payloads)

    async def extract(self, batch):
        self.calls += 1
        payload = self.payloads.pop(0)
        return ProfileModelResponse(
            payload=payload,
            latency_ms=1,
            request_bytes=1,
            backend="test",
            model=self.model,
        )


def _event(
    event_id="event-1",
    *,
    actor_id="member-1",
    occurred_at=100,
    text="我喜欢冷饮",
):
    return SocialEventEnvelope.create(
        event_id=event_id,
        event_type="platform.message",
        occurred_at=occurred_at,
        received_at=occurred_at,
        persona_id="persona",
        group_id="group-1",
        actor_id=actor_id,
        source_message_id=event_id,
        correlation_id=event_id,
        causation_id=None,
        payload={
            "platform": "qq",
            "text": text,
            "sender": {"id": actor_id, "name": actor_id},
            "mentions": [],
            "reply_to_actor_id": None,
        },
    )


def _service(tmp_path, client, *, batch_size=1):
    repository = ProfileRepository(tmp_path / "groupmate-social-runtime-v2.db")
    return ProfileService(
        repository=repository,
        extractor=ProfileExtractor(client, ProfileEvidencePolicy()),
        persona_id="persona",
        group_ids=("group-1",),
        batch_size=batch_size,
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


def test_profile_observe_only_wakes_style_worker_without_calling_it(tmp_path):
    class _StyleService:
        def __init__(self):
            self.wakes = 0
            self.calls = 0

        def wake(self):
            self.wakes += 1

        async def process_due(self, *, now=None):
            self.calls += 1

    client = _Client()
    style_service = _StyleService()
    repository = ProfileRepository(tmp_path / "groupmate-social-runtime-v2.db")
    service = ProfileService(
        repository=repository,
        extractor=ProfileExtractor(client, ProfileEvidencePolicy()),
        persona_id="persona",
        group_ids=("group-1",),
        batch_size=1,
        interval_seconds=600,
        clock=lambda: 100,
        style_service=style_service,
    )

    asyncio.run(service.observe(_event()))

    assert style_service.wakes == 1
    assert style_service.calls == 0
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
    portrait = service.repository.group_portrait("persona", "group-1")
    assert portrait is not None
    assert portrait.member_count == 1
    assert portrait.summary == "已形成1位成员画像，群体认知持续更新"


def test_observed_pattern_accumulates_evidence_across_batches(tmp_path):
    payloads = []
    summaries = ("经常帮群友排查问题", " 经常帮群友排查问题。 ", "经常帮群友排查问题！")
    for index, summary in enumerate(summaries, start=1):
        payloads.append(
            {
                "facts": [
                    {
                        "subject_id": "member-1",
                        "category": "behavior_pattern",
                        "summary": summary,
                        "source_kind": "observed_pattern",
                        "source_actor_id": "member-1",
                        "evidence_event_ids": [f"pattern-{index}"],
                        "confidence": 0.9,
                    }
                ],
                "episodes": [],
                "edges": [],
            }
        )
    service = _service(tmp_path, _SequenceClient(payloads))

    for index in range(1, 4):
        asyncio.run(
            service.observe(
                _event(
                    f"pattern-{index}",
                    occurred_at=100 + index,
                    text="又在帮忙排查问题",
                )
            )
        )
        asyncio.run(service.process_due(now=100 + index))

    facts = service.repository.facts("persona", "group-1", "member-1")
    assert len(facts) == 1
    assert facts[0].status == "confirmed"
    assert facts[0].injectable is True
    assert facts[0].evidence_count == 3
    assert facts[0].source_event_ids == (
        "pattern-1",
        "pattern-2",
        "pattern-3",
    )


def test_paraphrased_fact_reinforces_known_id_across_batches(tmp_path):
    first = {
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
    service = _service(tmp_path, _Client(first))
    asyncio.run(service.observe(_event("event-1", text="我喜欢冷饮")))
    asyncio.run(service.process_due(now=100))
    stored = service.repository.facts("persona", "group-1", "member-1")[0]

    service.extractor.client = _SequenceClient(
        [
            {
                "facts": [
                    {
                        "subject_id": "member-1",
                        "category": "preference",
                        "summary": "爱喝冰可乐",
                        "source_kind": "self_statement",
                        "source_actor_id": "member-1",
                        "evidence_event_ids": ["event-2"],
                        "confidence": 0.91,
                        "action": "reinforce",
                        "existing_id": stored.fact_id,
                    }
                ],
                "episodes": [],
                "edges": [],
            }
        ]
    )
    asyncio.run(
        service.observe(_event("event-2", occurred_at=120, text="还是冰可乐"))
    )
    asyncio.run(service.process_due(now=120))

    facts = service.repository.facts("persona", "group-1", "member-1")
    assert len(facts) == 1
    assert facts[0].fact_id == stored.fact_id
    assert facts[0].summary == "喜欢冷饮"
    assert facts[0].source_event_ids == ("event-1", "event-2")


def test_social_edge_accumulates_evidence_across_batches(tmp_path):
    payloads = []
    for index in range(1, 4):
        payloads.append(
            {
                "facts": [],
                "episodes": [],
                "edges": [
                    {
                        "source_member_id": "member-1",
                        "target_member_id": "member-2",
                        "relation_type": "supportive",
                        "direction": "directed",
                        "strength": 0.7,
                        "confidence": 0.9,
                        "evidence_event_ids": [f"edge-{index}-a"],
                    }
                ],
            }
        )
    service = _service(tmp_path, _SequenceClient(payloads), batch_size=2)

    for index in range(1, 4):
        asyncio.run(
            service.observe(
                _event(
                    f"edge-{index}-a",
                    actor_id="member-1",
                    occurred_at=100 + index * 2,
                    text="我来帮你看看",
                )
            )
        )
        asyncio.run(
            service.observe(
                _event(
                    f"edge-{index}-b",
                    actor_id="member-2",
                    occurred_at=101 + index * 2,
                    text="谢谢",
                )
            )
        )
        asyncio.run(service.process_due(now=101 + index * 2))

    edges = service.repository.edges("persona", "group-1")
    assert len(edges) == 1
    assert edges[0].status == "confirmed"
    assert edges[0].source_event_ids == (
        "edge-1-a",
        "edge-2-a",
        "edge-3-a",
    )


def test_provider_failure_keeps_observation_for_safe_retry(tmp_path):
    service = _service(
        tmp_path,
        _Client(error=ProfileModelError("profile_network_failed")),
    )
    asyncio.run(service.observe(_event()))

    asyncio.run(service.process_due(now=100))

    assert service.pending_count("group-1") == 1
    assert service.diagnostics("group-1") == ("profile_network_failed",)


def test_legal_empty_profile_result_is_a_visible_success(tmp_path):
    service = _service(tmp_path, _Client())
    asyncio.run(service.observe(_event()))

    asyncio.run(service.process_due(now=100))

    assert service.status("group-1") == {
        "enabled": True,
        "task_running": False,
        "pending_count": 0,
        "last_attempt_at": 100,
        "last_success_at": 100,
        "last_diagnostic": "profile_no_candidates",
    }


def test_unexpected_profile_worker_error_is_retried_and_reported(tmp_path):
    service = _service(tmp_path, _Client(error=RuntimeError("private detail")))
    asyncio.run(service.observe(_event()))

    asyncio.run(service.process_due(now=100))

    assert service.pending_count("group-1") == 1
    assert service.diagnostics("group-1") == ("profile_worker_failed",)
    assert service.status("group-1")["last_diagnostic"] == "profile_worker_failed"


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


def test_profile_observer_skips_commands_but_keeps_ambient_chat(tmp_path):
    class _Observer:
        def __init__(self):
            self.events = []

        async def observe(self, event):
            self.events.append(event)

    settings = SocialRuntimeSettings.from_mapping(
        {
            "enabled_groups": ["group-1"],
            "runtime_mode": "SHADOW",
            "generation_provider": "provider:text",
            "cognition_api_key": "sk-test",
        }
    )
    bridge = AstrBotSocialRuntimeBridge(object(), settings, tmp_path)
    observer = _Observer()
    bridge._profile_service = observer

    external_base = _event("external", text="bq 开心")
    external = replace(
        external_base,
        payload={
            **dict(external_base.payload),
            "interaction_owner": "EXTERNAL_PLUGIN",
            "social_eligible": False,
        },
    )
    ineligible_base = _event("ineligible", text="某个外部能力")
    ineligible = replace(
        ineligible_base,
        payload={**dict(ineligible_base.payload), "social_eligible": False},
    )
    own_query = _event("profile-query", text="查看我的画像")
    ambient = _event("ambient", text="今天有点热")

    async def scenario():
        for event in (external, ineligible, own_query, ambient):
            await bridge._observe_profile(event)

    asyncio.run(scenario())

    assert [event.event_id for event in observer.events] == ["ambient"]
