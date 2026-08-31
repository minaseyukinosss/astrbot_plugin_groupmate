from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest

from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.knowledge.repository import KnowledgeRepository
from groupmate.social_runtime.knowledge.seeds import (
    SeedImporter,
    load_bundled_seeds,
)
from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.settings import SocialRuntimeSettings
from groupmate.social_runtime.contracts import RuntimeMode


def _module():
    return importlib.import_module(
        "groupmate.social_runtime.knowledge.observation"
    )


def _event(
    event_id: str,
    text: str,
    *,
    group_id: str = "g1",
    actor_id: str = "human-1",
    scene_ref: str = "scene:1",
    is_self: bool = False,
    social_eligible: bool = True,
    owner: str = "UNKNOWN",
    segments=(),
    sender_role: str | None = None,
    automation_hint: str | None = None,
):
    payload = {
        "text": text,
        "is_self": is_self,
        "social_eligible": social_eligible,
        "interaction_owner": owner,
        "segments": list(segments) or [
            {"type": "text", "data": {"text": text}}
        ],
        "scene_ref": scene_ref,
    }
    if sender_role is not None:
        payload["sender_role"] = sender_role
    if automation_hint is not None:
        payload["automation_hint"] = automation_hint
    return SocialEventEnvelope.create(
        event_id=event_id,
        event_type="platform.message",
        occurred_at=100,
        received_at=101,
        persona_id="persona:1",
        group_id=group_id,
        actor_id=actor_id,
        source_message_id=event_id,
        correlation_id=event_id,
        causation_id=None,
        payload=payload,
    )


@pytest.mark.parametrize(
    ("event", "origin", "admitted"),
    [
        (_event("human", "鸣潮真好玩"), "human_chat", True),
        (_event("self", "鸣潮真好玩", is_self=True), "own_output", False),
        (
            _event("bot", "鸣潮真好玩", sender_role="bot"),
            "external_bot",
            False,
        ),
        (
            _event("unknown", "鸣潮真好玩", automation_hint="unknown"),
            "unknown_actor",
            False,
        ),
        (
            _event(
                "external-command",
                "xw帮助",
                social_eligible=False,
                owner="EXTERNAL_PLUGIN",
            ),
            "command",
            False,
        ),
        (_event("admin-command", "/groupmate status"), "command", False),
        (
            _event(
                "forward",
                "转发内容",
                segments=({"type": "forward", "data": {"id": "1"}},),
            ),
            "forward",
            False,
        ),
        (
            _event(
                "card",
                "卡片内容",
                segments=({"type": "json", "data": {"data": "{}"}},),
            ),
            "forward",
            False,
        ),
        (
            _event("link-only", "https://example.com/a"),
            "unknown_actor",
            False,
        ),
        (
            _event("human-link", "鸣潮这个讨论 https://example.com/a"),
            "human_chat",
            True,
        ),
    ],
)
def test_origin_classifier_is_fail_closed_for_automated_content(
    event, origin, admitted
):
    decision = _module().KnowledgeOriginClassifier().classify(event)

    assert decision.origin_class.value == origin
    assert decision.admitted is admitted


def _service(tmp_path, *, capacity=16):
    repository = KnowledgeRepository(
        tmp_path / "groupmate-social-runtime-v2.db"
    )
    SeedImporter(repository, clock=lambda: 90).import_all(load_bundled_seeds())
    service = _module().KnowledgeObservationService(
        repository=repository,
        group_ids=("g1", "g2"),
        install_salt="installation-secret",
        queue_capacity=capacity,
        clock=lambda: 200,
    )
    return repository, service


def test_observe_is_idempotent_and_close_drains_accepted_work(tmp_path):
    repository, service = _service(tmp_path)
    event = _event("definition", "潮游就是鸣潮")

    async def scenario():
        assert await service.observe(event) is True
        assert await service.observe(event) is False
        await service.close()

    asyncio.run(scenario())

    convention = repository.convention("g1", "潮游")
    assert convention is not None
    assert convention.status == "candidate"
    assert repository.observation_count(group_id="g1") == 1


def test_worker_failure_is_safely_diagnosed_and_retried(tmp_path, monkeypatch):
    repository, service = _service(tmp_path)
    original = repository.record_convention_evidence
    calls = 0

    def flaky(**values):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("secret model output must not escape")
        return original(**values)

    monkeypatch.setattr(repository, "record_convention_evidence", flaky)

    async def scenario():
        await service.observe(_event("retry", "潮游就是鸣潮"))
        await service.process_pending()
        first = service.health()
        await service.process_pending()
        return first, service.health()

    first, recovered = asyncio.run(scenario())

    assert first["last_diagnostic"] == "knowledge_worker_failed"
    assert first["pending_count"] == 1
    assert recovered["pending_count"] == 0
    assert recovered["last_diagnostic"] is None
    assert repository.convention("g1", "潮游") is not None


def test_pending_observation_is_recovered_after_service_restart(tmp_path):
    repository, first = _service(tmp_path)

    async def scenario():
        await first.observe(_event("recover", "潮游就是鸣潮"))
        recovered = _module().KnowledgeObservationService(
            repository=repository,
            group_ids=("g1", "g2"),
            install_salt="installation-secret",
            clock=lambda: 201,
        )
        await recovered.start()
        await recovered.process_pending()
        await recovered.close()

    asyncio.run(scenario())

    assert repository.observation_status_for_event("recover") == "admitted"
    assert repository.convention("g1", "潮游") is not None


def test_full_queue_evicts_lower_trust_work_without_blocking(tmp_path):
    repository, service = _service(tmp_path, capacity=1)

    async def scenario():
        low = await service.observe(_event("low", "潮游就是鸣潮"))
        high = await service.observe(
            _event("high", "潮游就是鸣潮", sender_role="admin")
        )
        return low, high, service.health()

    low, high, health = asyncio.run(scenario())

    assert low is True
    assert high is True
    assert health["pending_count"] == 1
    assert health["dropped_count"] == 1
    assert repository.observation_status_for_event("low") == "rejected"
    assert repository.observation_status_for_event("high") == "pending"


def test_definition_plus_consistent_use_in_another_scene_activates_group_alias(
    tmp_path,
):
    repository, service = _service(tmp_path)

    async def scenario():
        await service.observe(
            _event("define", "潮游就是鸣潮", scene_ref="scene:1")
        )
        await service.process_pending()
        await service.observe(
            _event("use", "潮游今天真好玩", scene_ref="scene:2")
        )
        await service.process_pending()

    asyncio.run(scenario())

    convention = repository.convention("g1", "潮游")
    assert convention is not None
    assert convention.status == "active"
    assert convention.distinct_actor_count == 1
    assert convention.distinct_scene_count == 2
    assert [
        item.entity_id
        for item in repository.aliases_for_text("潮游", group_id="g1")
    ] == ["game:wuthering-waves"]
    assert repository.aliases_for_text("潮游", group_id="g2") == ()


def test_two_people_three_scenes_are_counted_but_bot_spam_is_not(tmp_path):
    repository, service = _service(tmp_path)

    async def scenario():
        await service.observe(_event("d", "星游就是崩坏：星穹铁道"))
        await service.process_pending()
        await service.observe(
            _event("u1", "星游开玩", actor_id="human-2", scene_ref="scene:2")
        )
        await service.process_pending()
        await service.observe(
            _event("u2", "聊聊星游", actor_id="human-2", scene_ref="scene:3")
        )
        for index in range(3):
            await service.observe(
                _event(
                    f"bot-{index}",
                    "星游星游",
                    actor_id=f"bot-{index}",
                    sender_role="bot",
                    scene_ref=f"bot-scene:{index}",
                )
            )
        await service.process_pending()

    asyncio.run(scenario())

    convention = repository.convention("g1", "星游")
    assert convention is not None
    assert convention.status == "active"
    assert convention.distinct_actor_count == 2
    assert convention.distinct_scene_count == 3
    assert convention.evidence_count == 3


def test_admin_confirmation_activates_immediately(tmp_path):
    repository, service = _service(tmp_path)

    async def scenario():
        await service.observe(
            _event("admin", "潮游就是鸣潮", sender_role="owner")
        )
        await service.process_pending()

    asyncio.run(scenario())

    convention = repository.convention("g1", "潮游")
    assert convention is not None
    assert convention.status == "active"


def test_ordinary_human_game_mentions_update_affinity_but_bots_do_not(tmp_path):
    repository, service = _service(tmp_path)

    async def scenario():
        assert await service.observe(
            _event("human-game", "今天继续玩鸣潮", scene_ref="game-scene")
        ) is True
        assert await service.observe(
            _event(
                "bot-game",
                "鸣潮鸣潮",
                actor_id="bot-1",
                sender_role="bot",
                scene_ref="bot-scene",
            )
        ) is False
        await service.process_pending()

    asyncio.run(scenario())

    affinity = repository.topic_affinity(
        "g1", "game:wuthering-waves", now=200
    )
    assert affinity is not None
    assert affinity.qualified_mention_count == 1
    assert affinity.distinct_actor_count == 1
    assert affinity.distinct_scene_count == 1


def test_same_group_expression_can_resolve_differently_in_another_group(
    tmp_path,
):
    repository, service = _service(tmp_path)

    async def scenario():
        await service.observe(
            _event("g1-admin", "潮游就是鸣潮", sender_role="admin")
        )
        await service.observe(
            _event(
                "g2-admin",
                "潮游就是星铁",
                group_id="g2",
                sender_role="admin",
            )
        )
        await service.process_pending()

    asyncio.run(scenario())

    assert repository.convention("g1", "潮游").status == "active"
    assert repository.convention("g2", "潮游").status == "active"
    assert [
        item.entity_id
        for item in repository.aliases_for_text("潮游", group_id="g1")
    ] == ["game:wuthering-waves"]
    assert [
        item.entity_id
        for item in repository.aliases_for_text("潮游", group_id="g2")
    ] == ["game:honkai-star-rail"]


def test_single_scene_spam_stays_candidate_and_raw_actor_is_not_stored(
    tmp_path,
):
    repository, service = _service(tmp_path)

    async def scenario():
        await service.observe(_event("s1", "潮游就是鸣潮"))
        await service.process_pending()
        for index in range(3):
            await service.observe(_event(f"s{index + 2}", "潮游真好玩"))
        await service.process_pending()

    asyncio.run(scenario())

    convention = repository.convention("g1", "潮游")
    assert convention is not None
    assert convention.status == "candidate"
    assert convention.distinct_scene_count == 1
    assert "human-1" not in repository.knowledge_storage_text()


def test_conflicting_definition_marks_conventions_disputed_without_erasing_evidence(
    tmp_path,
):
    repository, service = _service(tmp_path)

    async def scenario():
        await service.observe(_event("d1", "潮游就是鸣潮", scene_ref="s1"))
        await service.process_pending()
        await service.observe(_event("u1", "潮游不错", scene_ref="s2"))
        await service.process_pending()
        await service.observe(
            _event("d2", "潮游就是星铁", actor_id="human-2", scene_ref="s3")
        )
        await service.process_pending()

    asyncio.run(scenario())

    conventions = repository.conventions_for_expression("g1", "潮游")
    assert {item.resolved_entity_id for item in conventions} == {
        "game:honkai-star-rail",
        "game:wuthering-waves",
    }
    assert {item.status for item in conventions} == {"disputed"}
    assert sum(item.evidence_count for item in conventions) == 3
    assert repository.aliases_for_text("潮游", group_id="g1") == ()


def test_inactive_convention_becomes_stale_after_ninety_days(tmp_path):
    repository, service = _service(tmp_path)

    async def scenario():
        await service.observe(_event("old", "潮游就是鸣潮"))
        await service.process_pending()

    asyncio.run(scenario())
    service.expire_stale(now=100 + 90 * 24 * 60 * 60 + 1)

    convention = repository.convention("g1", "潮游")
    assert convention is not None
    assert convention.status == "stale"


def test_bridge_observes_only_after_durable_runtime_ingest(tmp_path):
    class Manager:
        def __init__(self):
            self.inserted = True

        @staticmethod
        def group_mode(_group_id):
            return RuntimeMode.SHADOW

        async def ingest(self, _event):
            return SimpleNamespace(inserted=self.inserted)

        async def drain(self):
            return ()

    class Observer:
        def __init__(self):
            self.event_ids = []

        async def observe(self, event):
            self.event_ids.append(event.event_id)

    settings = SocialRuntimeSettings.from_mapping(
        {
            "enabled_groups": ["g1"],
            "runtime_mode": "SHADOW",
            "generation_provider": "provider:text",
        }
    )
    bridge = AstrBotSocialRuntimeBridge(object(), settings, tmp_path)
    manager = Manager()
    observer = Observer()
    bridge._started = True
    bridge._manager = manager
    bridge._knowledge_service = observer

    def raw(event_id):
        return {
            "message_id": event_id,
            "self_id": "bot-1",
            "group_id": "g1",
            "user_id": "human-1",
            "time": 100,
            "message": [
                {"type": "text", "data": {"text": "潮游就是鸣潮"}}
            ],
        }

    async def scenario():
        await bridge.handle_event(raw("accepted"))
        manager.inserted = False
        await bridge.handle_event(raw("duplicate"))
        await bridge.observe_event(raw("compatibility-only"))

    asyncio.run(scenario())

    assert observer.event_ids == ["qq:accepted"]
