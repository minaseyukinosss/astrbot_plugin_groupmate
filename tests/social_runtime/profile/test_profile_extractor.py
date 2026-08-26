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


def _observation():
    return ProfileObservation(
        event_id="event-1",
        persona_id="persona",
        group_id="group-1",
        actor_id="member-1",
        payload={
            "text": "我喜欢冷饮",
            "sender": {"id": "member-1", "name": "群友甲"},
            "mentions": [],
            "reply_to_actor_id": None,
        },
        occurred_at=100,
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
    assert result.diagnostic_code == "profile_output_invalid"
