from __future__ import annotations

import importlib

import pytest

from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.knowledge.repository import KnowledgeRepository
from groupmate.social_runtime.knowledge.seeds import (
    SeedImporter,
    load_bundled_seeds,
)
from groupmate.social_runtime.persistence.schema import connect_database


def _module():
    return importlib.import_module("groupmate.social_runtime.knowledge.resolver")


def _repository(tmp_path):
    repository = KnowledgeRepository(tmp_path / "knowledge.db")
    SeedImporter(repository, clock=lambda: 10).import_all(load_bundled_seeds())
    return repository


def _event(
    event_id: str,
    text: str,
    *,
    group_id: str = "g1",
    direct: bool = False,
):
    return SocialEventEnvelope.create(
        event_id=event_id,
        event_type="platform.message",
        occurred_at=100,
        received_at=101,
        persona_id="persona:1",
        group_id=group_id,
        actor_id="human:1",
        source_message_id=event_id,
        correlation_id=event_id,
        causation_id=None,
        payload={"text": text, "direct_address": direct},
    )


@pytest.mark.parametrize(
    ("text", "game_id"),
    [
        ("原神", "game:genshin-impact"),
        ("三角洲行动", "game:delta-force"),
        ("鸣潮", "game:wuthering-waves"),
        ("星铁", "game:honkai-star-rail"),
        ("绝区零", "game:zenless-zone-zero"),
    ],
)
def test_resolver_recognizes_five_bundled_games_and_common_names(
    tmp_path, text, game_id
):
    resolver = _module().KnowledgeEntityResolver(_repository(tmp_path))

    frame = resolver.resolve(_event("e", text), (), "g1", now=200)

    assert frame.game_ids == (game_id,)
    assert frame.resolved_entities[0].entity_id == game_id
    assert frame.confidence >= 0.75


def test_cross_game_term_requires_current_or_discourse_game_context(tmp_path):
    resolver = _module().KnowledgeEntityResolver(_repository(tmp_path))

    unresolved = resolver.resolve(_event("u", "歪了"), (), "g1", now=200)
    current = resolver.resolve(
        _event("c", "原神抽卡又歪了"), (), "g1", now=200
    )
    discourse = resolver.resolve(
        _event("d", "又歪了"),
        (_event("previous", "刚才在原神抽卡"),),
        "g1",
        now=200,
    )

    assert unresolved.resolved_terms == ()
    assert "ambiguous_term" in unresolved.ambiguity_codes
    assert any(item.term_id == "term:genshin:lose-5050" for item in current.resolved_terms)
    assert any(item.term_id == "term:genshin:lose-5050" for item in discourse.resolved_terms)


def test_stable_named_entities_can_identify_their_game_without_a_title(tmp_path):
    resolver = _module().KnowledgeEntityResolver(_repository(tmp_path))

    frame = resolver.resolve(
        _event("e", "旅行者还在提瓦特探索"), (), "g1", now=200
    )

    assert frame.game_ids == ("game:genshin-impact",)
    assert {item.entity_id for item in frame.resolved_entities} == {
        "entity:genshin:traveler",
        "entity:genshin:teyvat",
    }


def test_group_alias_is_scoped_to_its_group(tmp_path):
    repository = _repository(tmp_path)
    repository.put_group_alias(
        alias_id="alias:g1:bird",
        group_id="g1",
        entity_id="game:genshin-impact",
        normalized_alias="鸟游",
        evidence_observation_ids=("observation:1",),
        confidence=0.9,
        last_used_at=100,
        status="active",
    )
    resolver = _module().KnowledgeEntityResolver(repository)

    g1 = resolver.resolve(_event("g1", "鸟游真好玩"), (), "g1", now=200)
    g2 = resolver.resolve(
        _event("g2", "鸟游真好玩", group_id="g2"), (), "g2", now=200
    )

    assert g1.game_ids == ("game:genshin-impact",)
    assert g2.game_ids == ()


def test_high_affinity_cannot_create_a_match_without_alias_evidence(tmp_path):
    repository = _repository(tmp_path)
    repository.upsert_entity(
        entity_id="game:affinity-only",
        entity_type="game",
        canonical_name="不存在于消息中的游戏",
        canonical_game_id="game:affinity-only",
        status="active",
        now=10,
    )
    with connect_database(repository.path) as db:
        db.execute(
            "INSERT INTO group_topic_affinity(group_id,entity_id,"
            "qualified_mention_count,distinct_actor_count,distinct_scene_count,"
            "salience,first_seen_at,last_seen_at,updated_at) "
            "VALUES('g1','game:affinity-only',100,20,20,99,1,100,100)"
        )
    resolver = _module().KnowledgeEntityResolver(repository)

    frame = resolver.resolve(_event("e", "这个真好玩"), (), "g1", now=200)

    assert "game:affinity-only" not in frame.game_ids


def test_high_ambiguity_alias_without_context_stays_unresolved(tmp_path):
    resolver = _module().KnowledgeEntityResolver(_repository(tmp_path))

    frame = resolver.resolve(_event("e", "DF"), (), "g1", now=200)

    assert frame.game_ids == ()
    assert "ambiguous_entity" in frame.ambiguity_codes


def test_disabled_seed_is_not_resolved_even_if_projection_rows_remain(tmp_path):
    repository = _repository(tmp_path)
    with connect_database(repository.path) as db:
        db.execute(
            "UPDATE knowledge_seeds SET status='rejected' "
            "WHERE seed_id='game-semantic:genshin-impact'"
        )
    resolver = _module().KnowledgeEntityResolver(repository)

    frame = resolver.resolve(_event("e", "原神抽卡"), (), "g1", now=200)

    assert frame.game_ids == ()
    assert frame.resolved_entities == ()
    assert frame.resolved_terms == ()


@pytest.mark.parametrize(
    ("text", "relative_kind", "disclosure_kind"),
    [
        ("原神新版本怎么样", "new", "none"),
        ("原神下版本怎么样", "next", "none"),
        ("原神刚更新了什么", "recent_update", "release"),
        ("原神前瞻说了什么", "next", "preview"),
        ("原神有什么爆料", "unspecified", "rumor"),
        ("原神测试服改了什么", "unspecified", "test_server"),
        ("原神这期怎么样", "current", "none"),
    ],
)
def test_version_reference_is_relative_and_never_guesses_a_version_number(
    tmp_path, text, relative_kind, disclosure_kind
):
    resolver = _module().KnowledgeEntityResolver(_repository(tmp_path))

    frame = resolver.resolve(_event("e", text), (), "g1", now=200)

    assert frame.version_reference is not None
    assert frame.version_reference.game_id == "game:genshin-impact"
    assert frame.version_reference.relative_kind == relative_kind
    assert frame.version_reference.disclosure_kind == disclosure_kind
    assert not hasattr(frame.version_reference, "version_number")


def test_version_reference_with_multiple_games_is_explicitly_ambiguous(tmp_path):
    resolver = _module().KnowledgeEntityResolver(_repository(tmp_path))

    frame = resolver.resolve(
        _event("e", "原神和鸣潮下版本哪个更好"), (), "g1", now=200
    )

    assert set(frame.game_ids) == {
        "game:genshin-impact",
        "game:wuthering-waves",
    }
    assert frame.version_reference is None
    assert "ambiguous_game_for_version" in frame.ambiguity_codes
