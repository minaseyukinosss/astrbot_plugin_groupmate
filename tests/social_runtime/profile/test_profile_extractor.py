from __future__ import annotations

import asyncio

from groupmate.adapters.deepseek_profile import ProfileModelResponse
from groupmate.social_runtime.profile.contracts import ProfileObservation
from groupmate.social_runtime.profile.extractor import ProfileExtractor
from groupmate.social_runtime.profile.policy import ProfileEvidencePolicy


class _Client:
    model = "profile-test"

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def extract(self, batch):
        self.calls.append(batch)
        return ProfileModelResponse(
            payload=self.payload,
            latency_ms=10,
            request_bytes=100,
            backend="test",
            model=self.model,
        )


def _observation(
    event_id="event-1",
    actor_id="member-1",
    text="我喜欢冷饮",
    occurred_at=100,
):
    return ProfileObservation(
        event_id=event_id,
        persona_id="persona",
        group_id="group-1",
        actor_id=actor_id,
        payload={
            "text": text,
            "sender": {"id": actor_id, "name": actor_id},
            "mentions": [],
            "reply_to_actor_id": None,
        },
        occurred_at=occurred_at,
    )


def test_extractor_accepts_valid_self_statement_candidate():
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

    result = asyncio.run(
        ProfileExtractor(client, ProfileEvidencePolicy()).extract(
            (_observation(),)
        )
    )

    assert len(result.facts) == 1
    assert result.facts[0].summary == "喜欢冷饮"
    assert result.facts[0].injectable is True
    assert result.diagnostic_code is None


def test_extractor_rejects_unknown_category_without_partial_fact():
    client = _Client(
        {
            "facts": [
                {
                    "subject_id": "member-1",
                    "category": "invented_category",
                    "summary": "不可信结论",
                    "source_kind": "self_statement",
                    "source_actor_id": "member-1",
                    "evidence_event_ids": ["event-1"],
                    "confidence": 0.99,
                }
            ],
            "episodes": [],
            "edges": [],
        }
    )

    result = asyncio.run(
        ProfileExtractor(client, ProfileEvidencePolicy()).extract(
            (_observation(),)
        )
    )

    assert result.facts == ()
    assert result.diagnostic_code == "profile_fact_category_invalid"
    assert result.diagnostic_codes == ("profile_fact_category_invalid",)


def test_extractor_reports_the_invalid_candidate_kind_without_losing_valid_items():
    observations = (
        _observation("event-1", "member-1", "我Dota1玩得少", 100),
        _observation("event-2", "member-1", "我不太熟悉老版本机制", 110),
        _observation("event-3", "member-2", "我们经常一起研究机制", 120),
    )
    client = _Client(
        {
            "facts": [
                {
                    "subject_id": "member-1",
                    "category": "skill",
                    "summary": "Dota1经验较少",
                    "source_kind": "self_statement",
                    "source_actor_id": "member-1",
                    "evidence_event_ids": ["event-1", "event-2"],
                    "confidence": 0.93,
                }
            ],
            "episodes": [],
            "edges": [
                {
                    "source_member_id": "member-1",
                    "target_member_id": "member-2",
                    "relation_type": "friend",
                    "direction": "bidirectional",
                    "strength": 0.72,
                    "confidence": 0.92,
                    "evidence_event_ids": ["event-1", "event-2", "event-3"],
                }
            ],
        }
    )

    result = asyncio.run(
        ProfileExtractor(client, ProfileEvidencePolicy()).extract(observations)
    )

    assert [fact.summary for fact in result.facts] == ["Dota1经验较少"]
    assert result.edges == ()
    assert result.diagnostic_code == "profile_edge_relation_type_invalid"
    assert result.diagnostic_codes == ("profile_edge_relation_type_invalid",)


def test_extractor_rejects_model_source_kind_outside_the_declared_vocabulary():
    client = _Client(
        {
            "facts": [
                {
                    "subject_id": "member-1",
                    "category": "interest",
                    "summary": "喜欢研究游戏机制",
                    "source_kind": "personal_statement",
                    "source_actor_id": "member-1",
                    "evidence_event_ids": ["event-1"],
                    "confidence": 0.94,
                }
            ],
            "episodes": [],
            "edges": [],
        }
    )

    result = asyncio.run(
        ProfileExtractor(client, ProfileEvidencePolicy()).extract(
            (_observation(),)
        )
    )

    assert result.facts == ()
    assert result.diagnostic_code == "profile_fact_source_kind_invalid"


def test_extractor_validates_episode_and_repeated_social_edge_evidence():
    observations = (
        _observation("event-1", "member-1", "这个问题我来看看", 100),
        _observation("event-2", "member-2", "我也一起排查", 110),
        _observation("event-3", "member-1", "已经修好了", 120),
    )
    client = _Client(
        {
            "facts": [],
            "episodes": [
                {
                    "title": "一起修复问题",
                    "summary": "两位成员共同排查并修复了问题",
                    "participants": ["member-1", "member-2"],
                    "episode_type": "shared_achievement",
                    "valence": 0.7,
                    "importance": 0.85,
                    "confidence": 0.94,
                    "evidence_event_ids": ["event-1", "event-2", "event-3"],
                }
            ],
            "edges": [
                {
                    "source_member_id": "member-1",
                    "target_member_id": "member-2",
                    "relation_type": "technical_peer",
                    "direction": "bidirectional",
                    "strength": 0.72,
                    "confidence": 0.92,
                    "evidence_event_ids": ["event-1", "event-2", "event-3"],
                }
            ],
        }
    )

    result = asyncio.run(
        ProfileExtractor(client, ProfileEvidencePolicy()).extract(observations)
    )

    assert result.episodes[0].title == "一起修复问题"
    assert result.episodes[0].status == "confirmed"
    assert result.edges[0].relation_type == "technical_peer"
    assert result.edges[0].status == "confirmed"
    assert result.diagnostic_code is None
def test_fact_identity_keeps_source_kind_separate():
    observation = _observation("event-1", "member-1")
    shared = {
        "subject_id": "member-1",
        "category": "preference",
        "summary": "喜欢冷饮",
        "source_actor_id": "member-1",
        "evidence_event_ids": ["event-1"],
    }

    self_statement = ProfileExtractor._candidate_id(
        {**shared, "source_kind": "self_statement"}, observation
    )
    hearsay = ProfileExtractor._candidate_id(
        {**shared, "source_kind": "third_party_claim"}, observation
    )

    assert self_statement != hearsay


def test_fact_identity_keeps_semantic_programming_symbols():
    observation = _observation("event-1", "member-1")
    shared = {
        "subject_id": "member-1",
        "category": "skill",
        "source_kind": "self_statement",
        "source_actor_id": "member-1",
        "evidence_event_ids": ["event-1"],
    }

    c_sharp = ProfileExtractor._candidate_id(
        {**shared, "summary": "擅长 C#"}, observation
    )
    plain_c = ProfileExtractor._candidate_id(
        {**shared, "summary": "擅长 C"}, observation
    )

    assert c_sharp != plain_c
