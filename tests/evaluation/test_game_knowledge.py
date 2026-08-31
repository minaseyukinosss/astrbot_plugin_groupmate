from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.knowledge.observation import (
    KnowledgeObservationService,
    KnowledgeOriginClassifier,
)
from groupmate.social_runtime.knowledge.contracts import OriginClass
from groupmate.social_runtime.knowledge.repository import KnowledgeRepository
from groupmate.social_runtime.knowledge.retrieval import KnowledgeNeedAssessor
from groupmate.social_runtime.knowledge.resolver import KnowledgeEntityResolver
from groupmate.social_runtime.knowledge.seeds import SeedImporter, load_bundled_seeds


def _metrics(records):
    module = importlib.import_module("eval.knowledge")
    return module.game_knowledge_metrics(records)


def _record(**overrides):
    value = {
        "understanding_correct": True,
        "high_confidence_merge": False,
        "merge_correct": True,
        "cross_group_leak": False,
        "promoted_origin": None,
        "temporal_need_expected": False,
        "temporal_need_detected": False,
    }
    value.update(overrides)
    return value


def test_metrics_count_unsafe_promotions_and_measure_temporal_recall():
    result = _metrics(
        (
            _record(
                high_confidence_merge=True,
                merge_correct=False,
                promoted_origin="external_bot",
                temporal_need_expected=True,
                temporal_need_detected=True,
            ),
            _record(
                understanding_correct=False,
                high_confidence_merge=True,
                merge_correct=True,
                cross_group_leak=True,
                promoted_origin="command",
                temporal_need_expected=True,
                temporal_need_detected=False,
            ),
            _record(promoted_origin="human_chat"),
        )
    )

    assert result["understanding_accuracy"] == pytest.approx(2 / 3)
    assert result["high_confidence_wrong_merge_rate"] == 0.5
    assert result["cross_group_leaks"] == 1
    assert result["bot_promotions"] == 1
    assert result["command_promotions"] == 1
    assert result["temporal_need_recall"] == 0.5


def test_metrics_return_zero_for_empty_denominators():
    result = _metrics((_record(),))

    assert result == {
        "understanding_accuracy": 1.0,
        "high_confidence_wrong_merge_rate": 0.0,
        "cross_group_leaks": 0,
        "bot_promotions": 0,
        "command_promotions": 0,
        "temporal_need_recall": 0.0,
    }


@pytest.mark.parametrize(
    "record",
    [
        {},
        _record(understanding_correct=1),
        _record(promoted_origin="unclassified_feed"),
        _record(temporal_need_expected=False, temporal_need_detected=True),
    ],
)
def test_metrics_reject_malformed_records(record):
    with pytest.raises(ValueError, match="record"):
        _metrics((record,))


def test_corpus_runner_surfaces_real_safety_probe_failures():
    report = importlib.import_module(
        "eval.knowledge"
    ).run_frozen_game_knowledge_corpus(
        (),
        resolver=object(),
        assessor=object(),
        safety_records=(
            _record(cross_group_leak=True),
            _record(promoted_origin="external_bot"),
            _record(promoted_origin="command"),
        ),
    )

    assert report["metrics"]["cross_group_leaks"] == 1
    assert report["metrics"]["bot_promotions"] == 1
    assert report["metrics"]["command_promotions"] == 1


def test_safety_probe_uses_fixture_origin_and_requires_positive_scope_control():
    mislabeled_admission = SimpleNamespace(
        admitted=True, origin_class=OriginClass.HUMAN_CHAT
    )
    bot_record = _promotion_record(
        mislabeled_admission, OriginClass.EXTERNAL_BOT
    )
    assert bot_record["promoted_origin"] == "external_bot"

    resolver = SimpleNamespace(
        resolve=lambda *_args: SimpleNamespace(game_ids=())
    )
    with pytest.raises(AssertionError, match="positive control"):
        _cross_group_safety_record(
            resolver,
            _knowledge_event("same", "潮游", group_id="safety:g1"),
            _knowledge_event("other", "潮游", group_id="safety:g2"),
            "game:wuthering-waves",
        )


def test_frozen_game_understanding_corpus_passes_gate_one(tmp_path):
    corpus_path = (
        Path(__file__).parents[2]
        / "scenarios"
        / "game_knowledge_understanding.jsonl"
    )
    cases = [
        json.loads(line)
        for line in corpus_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(cases) == 196
    assert len({case["case_id"] for case in cases}) == len(cases)
    assert all(
        set(case)
        == {"case_id", "group_id", "occurred_at", "text", "context", "expected"}
        and set(case["expected"])
        == {"games", "entities", "terms", "version", "need", "ambiguity"}
        for case in cases
    )
    categories = {
        prefix: sum(case["case_id"].startswith(prefix + ":") for case in cases)
        for prefix in ("stable", "ambiguous", "version", "control")
    }
    assert categories == {
        "stable": 144,
        "ambiguous": 26,
        "version": 16,
        "control": 10,
    }
    ambiguity_cases = [
        case for case in cases if case["case_id"].startswith("ambiguous:")
    ]
    assert len({case["text"] for case in ambiguity_cases}) == 26
    assert sum(bool(case["context"]) for case in ambiguity_cases) == 11
    assert sum(case["expected"]["ambiguity"] for case in ambiguity_cases) == 15
    stable_games = {
        game_id: sum(
            case["case_id"].startswith("stable:")
            and case["expected"]["games"] == [game_id]
            for case in cases
        )
        for game_id in {
            "game:delta-force",
            "game:wuthering-waves",
            "game:honkai-star-rail",
            "game:zenless-zone-zero",
        }
    }
    assert set(stable_games.values()) == {36}

    repository = KnowledgeRepository(tmp_path / "knowledge-v4.db")
    SeedImporter(repository, clock=lambda: 1_786_000_000).import_all(
        load_bundled_seeds()
    )
    report = importlib.import_module(
        "eval.knowledge"
    ).run_frozen_game_knowledge_corpus(
        cases,
        resolver=KnowledgeEntityResolver(repository),
        assessor=KnowledgeNeedAssessor(),
        safety_records=_safety_records(repository),
    )
    result = report["metrics"]
    assert result["understanding_accuracy"] >= 0.95
    assert result["high_confidence_wrong_merge_rate"] < 0.01
    assert result["cross_group_leaks"] == 0
    assert result["bot_promotions"] == 0
    assert result["command_promotions"] == 0
    assert result["temporal_need_recall"] == 1.0
    assert all(
        item["latency_ms"] >= 0 and item["error_type"] is None
        for item in report["case_results"]
    )


def _safety_records(repository):
    classifier = KnowledgeOriginClassifier()
    bot = classifier.classify(
        _knowledge_event("bot", "潮游就是鸣潮", sender_role="bot")
    )
    command = classifier.classify(
        _knowledge_event(
            "command",
            "/潮游就是鸣潮",
            social_eligible=False,
            owner="EXTERNAL_PLUGIN",
        )
    )
    own_output = classifier.classify(
        _knowledge_event("self", "潮游就是鸣潮", is_self=True)
    )
    service = KnowledgeObservationService(
        repository=repository,
        group_ids=("safety:g1", "safety:g2"),
        install_salt="frozen-evaluation-install-salt",
        clock=lambda: 1_786_000_000,
    )

    async def learn_group_alias():
        await service.observe(
            _knowledge_event(
                "define",
                "潮游就是鸣潮",
                group_id="safety:g1",
                scene_ref="scene:definition",
            )
        )
        await service.process_pending()
        await service.observe(
            _knowledge_event(
                "use",
                "潮游今天真好玩",
                group_id="safety:g1",
                scene_ref="scene:use",
            )
        )
        await service.process_pending()

    asyncio.run(learn_group_alias())
    resolver = KnowledgeEntityResolver(repository)
    same_group = _knowledge_event(
        "same-group", "潮游", group_id="safety:g1"
    )
    other_group = _knowledge_event(
        "other-group", "潮游", group_id="safety:g2"
    )
    return (
        _promotion_record(bot, OriginClass.EXTERNAL_BOT),
        _promotion_record(command, OriginClass.COMMAND),
        _promotion_record(own_output, OriginClass.OWN_OUTPUT),
        _cross_group_safety_record(
            resolver,
            same_group,
            other_group,
            "game:wuthering-waves",
        ),
    )


def _promotion_record(decision, expected_origin):
    return _record(
        promoted_origin=(expected_origin.value if decision.admitted else None)
    )


def _cross_group_safety_record(
    resolver, same_group_event, other_group_event, target_game_id
):
    same_group = resolver.resolve(
        same_group_event,
        (),
        str(same_group_event.group_id),
        1_786_000_000,
    )
    if target_game_id not in same_group.game_ids:
        raise AssertionError("cross-group probe positive control did not resolve")
    other_group = resolver.resolve(
        other_group_event,
        (),
        str(other_group_event.group_id),
        1_786_000_000,
    )
    return _record(cross_group_leak=target_game_id in other_group.game_ids)


def _knowledge_event(
    event_id,
    text,
    *,
    group_id="safety:g1",
    scene_ref="scene:1",
    sender_role=None,
    is_self=False,
    social_eligible=True,
    owner="UNKNOWN",
):
    payload = {
        "text": text,
        "segments": [{"type": "text", "data": {"text": text}}],
        "scene_ref": scene_ref,
        "social_eligible": social_eligible,
        "interaction_owner": owner,
        "is_self": is_self,
    }
    if sender_role is not None:
        payload["sender_role"] = sender_role
    return SocialEventEnvelope.create(
        event_id=f"safety:{event_id}",
        event_type="platform.message",
        occurred_at=1_786_000_000,
        received_at=1_786_000_000,
        persona_id="groupmate:default",
        group_id=group_id,
        actor_id="anonymous-member",
        source_message_id=event_id,
        correlation_id=f"safety:{event_id}",
        causation_id=None,
        payload=payload,
    )
