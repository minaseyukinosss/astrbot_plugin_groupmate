from __future__ import annotations

import asyncio

from groupmate.adapters.deepseek_profile import ProfileModelError, ProfileModelResponse
from groupmate.adapters.profile_query import parse_profile_command
from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.profile.extractor import ProfileExtractor
from groupmate.social_runtime.profile.policy import ProfileEvidencePolicy
from groupmate.social_runtime.profile.repository import ProfileRepository
from groupmate.social_runtime.profile.retrieval import ProfileRetriever
from groupmate.social_runtime.profile.service import ProfileService


class _Client:
    model = "acceptance-profile"

    def __init__(self, payload=None, error=None):
        self.payload = payload or {"facts": [], "episodes": [], "edges": []}
        self.error = error

    async def extract(self, _batch):
        if self.error is not None:
            raise self.error
        return ProfileModelResponse(
            payload=self.payload,
            latency_ms=1,
            request_bytes=128,
            backend="acceptance",
            model=self.model,
        )


def _event(event_id: str, actor_id: str, text: str, *, group_id="group-1", at=100):
    return SocialEventEnvelope.create(
        event_id=event_id,
        event_type="platform.message",
        occurred_at=at,
        received_at=at,
        persona_id="persona",
        group_id=group_id,
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


def _service(path, client, *, groups=("group-1",), batch_size=1):
    repository = ProfileRepository(path)
    return ProfileService(
        repository=repository,
        extractor=ProfileExtractor(client, ProfileEvidencePolicy()),
        persona_id="persona",
        group_ids=groups,
        batch_size=batch_size,
        interval_seconds=600,
        clock=lambda: 200,
    )


def test_confirmed_self_knowledge_accumulates_and_reaches_reply_context(tmp_path):
    service = _service(
        tmp_path / "runtime.db",
        _Client({
            "facts": [{
                "subject_id": "member-1",
                "category": "boundary",
                "summary": "不喜欢含糊解释",
                "source_kind": "self_statement",
                "source_actor_id": "member-1",
                "evidence_event_ids": ["event-1"],
                "confidence": 0.96,
            }],
            "episodes": [],
            "edges": [],
        }),
    )
    asyncio.run(service.observe(_event("event-1", "member-1", "我不喜欢含糊解释")))
    asyncio.run(service.process_due(now=200))

    retrieval = ProfileRetriever(service.repository).for_message(
        _event("event-current", "member-1", "继续说", at=210)
    )

    assert "不喜欢含糊解释" in retrieval.prompt_text


def test_third_party_claim_stays_non_injectable(tmp_path):
    service = _service(
        tmp_path / "runtime.db",
        _Client({
            "facts": [{
                "subject_id": "member-1",
                "category": "preference",
                "summary": "据说只喜欢深夜聊天",
                "source_kind": "third_party_claim",
                "source_actor_id": "member-2",
                "evidence_event_ids": ["event-2"],
                "confidence": 0.99,
            }],
            "episodes": [],
            "edges": [],
        }),
        batch_size=2,
    )
    asyncio.run(service.observe(_event("event-1", "member-1", "大家好", at=100)))
    asyncio.run(service.observe(_event("event-2", "member-2", "他只喜欢深夜聊天", at=101)))
    asyncio.run(service.process_due(now=200))

    fact = service.repository.facts("persona", "group-1", "member-1")[0]
    retrieval = ProfileRetriever(service.repository).for_message(
        _event("event-current", "member-1", "继续说", at=210)
    )

    assert (fact.status, fact.injectable) == ("proposed", False)
    assert fact.summary not in retrieval.prompt_text


def test_profile_provider_failure_keeps_local_query_and_retry_available(tmp_path):
    service = _service(
        tmp_path / "runtime.db",
        _Client(error=ProfileModelError("profile_provider_unavailable")),
    )
    asyncio.run(service.observe(_event("event-1", "member-1", "我喜欢清晰说明")))
    asyncio.run(service.process_due(now=200))

    assert service.pending_count("group-1") == 1
    assert service.diagnostics("group-1") == ("profile_provider_unavailable",)
    assert parse_profile_command("查看我的画像").kind == "show_self"


def test_same_actor_profile_facts_remain_group_local(tmp_path):
    path = tmp_path / "runtime.db"
    service = _service(
        path,
        _Client({
            "facts": [{
                "subject_id": "member-1",
                "category": "interest",
                "summary": "只在一群讨论插件开发",
                "source_kind": "self_statement",
                "source_actor_id": "member-1",
                "evidence_event_ids": ["event-g1"],
                "confidence": 0.95,
            }],
            "episodes": [],
            "edges": [],
        }),
        groups=("group-1", "group-2"),
    )
    asyncio.run(service.observe(_event("event-g1", "member-1", "我只在一群讨论插件开发")))
    asyncio.run(service.process_due(now=200))

    repository = ProfileRepository(path)
    assert repository.facts("persona", "group-1", "member-1")
    assert repository.facts("persona", "group-2", "member-1") == ()
