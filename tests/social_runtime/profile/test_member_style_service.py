from __future__ import annotations

import asyncio

from groupmate.adapters.deepseek_member_style import (
    MemberStyleModelError,
    MemberStyleModelResponse,
)
from groupmate.social_runtime.profile.contracts import ProfileObservation
from groupmate.social_runtime.profile.repository import ProfileRepository
from groupmate.social_runtime.profile.style_repository import MemberStyleRepository
from groupmate.social_runtime.profile.style_service import MemberStyleService


class _Client:
    model = "style-test"

    def __init__(self, *, error=None):
        self.error = error
        self.calls = []

    async def distill(self, batch):
        self.calls.append(batch)
        if self.error is not None:
            raise self.error
        evidence = [item["event_id"] for item in batch["events"]]
        return MemberStyleModelResponse(
            payload={
                "opening_patterns": ["先直接表态"],
                "progression_patterns": ["短句后补充具体理由"],
                "closing_patterns": ["自然收口"],
                "length_rhythm": "一到两句为主",
                "directness": "直接但不替他人判断",
                "disagreement_style": "先指出问题再解释",
                "play_style": "从现场用词形成调侃",
                "care_style": "关心时给具体动作",
                "addressing_style": "需要明确对象时才称呼",
                "particles_punctuation": "少量语气词",
                "stable_traits": [
                    {"description": "结论在前", "evidence_event_ids": evidence[:3]}
                ],
                "occasional_traits": ["偶尔省略主语"],
            },
            latency_ms=1,
            request_bytes=100,
            backend="test",
            model=self.model,
        )


def _text(index: int) -> str:
    variants = (
        "这个问题先看结论，后面再说原因",
        "笑死，这个说法还真挺有意思",
        "记得喝水休息，别一直熬着",
    )
    return variants[index % len(variants)]


def _queue(repository, *, count: int, start_index: int = 0):
    for offset in range(count):
        index = start_index + offset
        repository.enqueue_observation(
            ProfileObservation(
                event_id=f"event-{index}",
                persona_id="persona",
                group_id="group-1",
                actor_id="member-1",
                payload={"text": _text(index)},
                occurred_at=100 + (index % 5) * 86_400 + index,
            )
        )


def _service(tmp_path, client):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    profile_repository = ProfileRepository(path)
    style_repository = MemberStyleRepository(path)
    style_repository.set_enabled(
        "group-1", "member-1", enabled=True, updated_by="admin", now=1
    )
    return (
        MemberStyleService(repository=style_repository, client=client),
        profile_repository,
        style_repository,
    )


def test_service_does_not_call_model_below_maturity(tmp_path):
    client = _Client()
    service, observations, styles = _service(tmp_path, client)
    _queue(observations, count=39)

    asyncio.run(service.process_due(now=1_000_000))

    assert client.calls == []
    assert styles.latest_ready("group-1", "member-1") is None


def test_service_publishes_first_ready_version_at_maturity(tmp_path):
    client = _Client()
    service, observations, styles = _service(tmp_path, client)
    _queue(observations, count=40)

    asyncio.run(service.process_due(now=1_000_000))

    style = styles.latest_ready("group-1", "member-1")
    assert len(client.calls) == 1
    assert style is not None
    assert style.version == 1
    assert style.stable_traits == ("结论在前",)
    assert style.eligible_message_count == 40


def test_provider_failure_keeps_previous_ready_version(tmp_path):
    first_client = _Client()
    service, observations, styles = _service(tmp_path, first_client)
    _queue(observations, count=40)
    asyncio.run(service.process_due(now=1_000_000))
    first = styles.latest_ready("group-1", "member-1")

    service.client = _Client(error=MemberStyleModelError("member_style_network_failed"))
    _queue(observations, count=20, start_index=40)
    asyncio.run(service.process_due(now=2_000_000))

    assert styles.latest_ready("group-1", "member-1") == first
    assert service.member_status("group-1", "member-1")["last_diagnostic"] == "member_style_network_failed"


def test_enough_incremental_evidence_publishes_version_two(tmp_path):
    client = _Client()
    service, observations, styles = _service(tmp_path, client)
    _queue(observations, count=40)
    asyncio.run(service.process_due(now=1_000_000))
    _queue(observations, count=20, start_index=40)

    asyncio.run(service.process_due(now=2_000_000))

    assert styles.latest_ready("group-1", "member-1").version == 2
    assert len(client.calls) == 2
