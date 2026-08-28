from __future__ import annotations

import importlib

import pytest

from groupmate.social_runtime.knowledge.contracts import (
    KnowledgeObservation,
    SourceEvidence,
)
from groupmate.social_runtime.persistence.schema import connect_database


def _repository(path):
    module = importlib.import_module(
        "groupmate.social_runtime.knowledge.repository"
    )
    return module.KnowledgeRepository(path)


def _observation(
    *,
    observation_id: str,
    event_id: str,
    author_ref: str | None,
    content_hash: str,
    origin_class: str = "human_chat",
    group_id: str = "g1",
    occurred_at: int = 100,
):
    return KnowledgeObservation.create(
        observation_id=observation_id,
        origin_class=origin_class,
        scope_kind="group",
        group_id=group_id,
        author_ref=author_ref,
        source_event_id=event_id,
        source_id=None,
        entity_hint="原神",
        safe_summary="自然聊天中出现一个游戏实体",
        content_hash=content_hash,
        occurred_at=occurred_at,
        recorded_at=occurred_at + 1,
        status="pending",
    )


def _put_game(repository, entity_id: str, name: str):
    repository.upsert_entity(
        entity_id=entity_id,
        entity_type="game",
        canonical_name=name,
        canonical_game_id=entity_id,
        status="active",
        now=100,
    )


def _source(
    *,
    evidence_id: str = "evidence:1",
    source_id: str = "source:official-news",
    url: str = "https://game.example.com/news?id=1&utm_source=chat",
    publisher: str = "Example Game",
    source_class: str = "official",
    fetched_at: int = 200,
    published_at: int | None = 190,
    content_hash: str = "a" * 64,
):
    return SourceEvidence.create(
        evidence_id=evidence_id,
        source_id=source_id,
        canonical_url=url,
        domain="game.example.com",
        publisher=publisher,
        source_class=source_class,
        title="版本公告",
        published_at=published_at,
        fetched_at=fetched_at,
        evidence_excerpt="官方发布了版本公告。",
        content_hash=content_hash,
    )


def test_source_upsert_deduplicates_canonical_url_and_refreshes_content(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    repository = _repository(path)

    assert repository.upsert_source(_source()) == "source:official-news"
    assert repository.upsert_source(
        _source(
            evidence_id="evidence:2",
            source_id="source:duplicate",
            url="https://game.example.com/news?utm_medium=bot&id=1",
            fetched_at=300,
            published_at=None,
            content_hash="b" * 64,
        )
    ) == "source:official-news"

    with connect_database(path) as db:
        rows = db.execute("SELECT * FROM knowledge_sources").fetchall()
    assert len(rows) == 1
    assert rows[0]["canonical_url"] == "https://game.example.com/news?id=1"
    assert rows[0]["fetched_at"] == 300
    assert rows[0]["content_hash"] == "b" * 64
    assert rows[0]["published_at"] == 190


def test_source_upsert_deduplicates_same_publisher_content_hash(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    repository = _repository(path)
    repository.upsert_source(_source())

    source_id = repository.upsert_source(
        _source(
            evidence_id="evidence:mirror",
            source_id="source:mirror",
            url="https://game.example.com/archive?id=99",
        )
    )

    assert source_id == "source:official-news"
    with connect_database(path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM knowledge_sources"
        ).fetchone()[0] == 1


def test_source_identity_cannot_be_upgraded_or_republished_by_upsert(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    repository = _repository(path)
    repository.upsert_source(
        _source(publisher="Community Mirror", source_class="unofficial")
    )

    repository.upsert_source(
        _source(
            evidence_id="evidence:forged-upgrade",
            source_id="source:forged-upgrade",
            publisher="Example Game",
            source_class="official",
            fetched_at=300,
            content_hash="c" * 64,
        )
    )

    with connect_database(path) as db:
        row = db.execute(
            "SELECT publisher,source_class,fetched_at,content_hash "
            "FROM knowledge_sources"
        ).fetchone()
    assert tuple(row) == (
        "Community Mirror",
        "unofficial",
        300,
        "c" * 64,
    )


def test_source_id_cannot_be_rebound_to_another_canonical_url(tmp_path):
    repository = _repository(tmp_path / "groupmate-social-runtime-v2.db")
    repository.upsert_source(_source())

    with pytest.raises(ValueError, match="another canonical URL"):
        repository.upsert_source(
            _source(
                evidence_id="evidence:rebound",
                url="https://game.example.com/news?id=2",
                content_hash="d" * 64,
            )
        )


def test_observation_append_is_idempotent_by_identity_and_scoped_content_hash(
    tmp_path,
):
    repository = _repository(tmp_path / "groupmate-social-runtime-v2.db")
    observation = _observation(
        observation_id="observation:1",
        event_id="event:1",
        author_ref="author:1",
        content_hash="a" * 64,
    )
    same_content = _observation(
        observation_id="observation:2",
        event_id="event:2",
        author_ref="author:2",
        content_hash="a" * 64,
    )

    assert repository.append_observation(observation) is True
    assert repository.append_observation(observation) is False
    assert repository.append_observation(same_content) is False
    assert repository.observation_count(group_id="g1") == 1


def test_group_aliases_never_cross_group_scope(tmp_path):
    repository = _repository(tmp_path / "groupmate-social-runtime-v2.db")
    _put_game(repository, "game:one", "游戏一")
    _put_game(repository, "game:two", "游戏二")
    repository.put_alias(
        alias_id="alias:global",
        entity_id="game:one",
        normalized_alias="游戏一",
        alias_kind="official",
        ambiguity_level="none",
        source_id="seed:one:v1",
        status="active",
    )
    repository.put_group_alias(
        alias_id="alias:g1",
        group_id="g1",
        entity_id="game:one",
        normalized_alias="鸟游",
        evidence_observation_ids=("observation:g1",),
        confidence=0.9,
        last_used_at=100,
        status="active",
    )
    repository.put_group_alias(
        alias_id="alias:g2",
        group_id="g2",
        entity_id="game:two",
        normalized_alias="鸟游",
        evidence_observation_ids=("observation:g2",),
        confidence=0.8,
        last_used_at=100,
        status="active",
    )

    assert [
        (item.scope_kind, item.entity_id)
        for item in repository.aliases_for_text("游戏一", group_id="g2")
    ] == [("global", "game:one")]
    assert [
        (item.scope_kind, item.entity_id)
        for item in repository.aliases_for_text("鸟游", group_id="g1")
    ] == [("group", "game:one")]
    assert [
        (item.scope_kind, item.entity_id)
        for item in repository.aliases_for_text("鸟游", group_id="g2")
    ] == [("group", "game:two")]
    assert repository.aliases_for_text("鸟游", group_id="g3") == ()


def test_qualified_mentions_are_idempotent_and_decay_with_seven_day_half_life(
    tmp_path,
):
    repository = _repository(tmp_path / "groupmate-social-runtime-v2.db")
    _put_game(repository, "game:genshin-impact", "原神")
    observations = (
        _observation(
            observation_id="observation:1",
            event_id="event:1",
            author_ref="author:1",
            content_hash="1" * 64,
        ),
        _observation(
            observation_id="observation:2",
            event_id="event:2",
            author_ref="author:2",
            content_hash="2" * 64,
        ),
        _observation(
            observation_id="observation:3",
            event_id="event:3",
            author_ref="author:2",
            content_hash="3" * 64,
        ),
    )
    for observation in observations:
        assert repository.append_observation(observation) is True

    assert repository.record_qualified_mention(
        "observation:1", "game:genshin-impact", "scene:1"
    ) is True
    assert repository.record_qualified_mention(
        "observation:2", "game:genshin-impact", "scene:1"
    ) is True
    assert repository.record_qualified_mention(
        "observation:3", "game:genshin-impact", "scene:2"
    ) is True
    assert repository.record_qualified_mention(
        "observation:1", "game:genshin-impact", "scene:1"
    ) is False

    current = repository.topic_affinity(
        "g1", "game:genshin-impact", now=100
    )
    decayed = repository.topic_affinity(
        "g1", "game:genshin-impact", now=604_900
    )

    assert current is not None
    assert current.qualified_mention_count == 3
    assert current.distinct_actor_count == 2
    assert current.distinct_scene_count == 2
    assert current.salience == pytest.approx(3.0)
    assert decayed is not None
    assert decayed.salience == pytest.approx(1.5)


def test_non_human_observation_cannot_strengthen_topic_affinity(tmp_path):
    repository = _repository(tmp_path / "groupmate-social-runtime-v2.db")
    _put_game(repository, "game:genshin-impact", "原神")
    bot_observation = _observation(
        observation_id="observation:bot",
        event_id="event:bot",
        author_ref=None,
        content_hash="b" * 64,
        origin_class="external_bot",
    )
    assert repository.append_observation(bot_observation) is True

    with pytest.raises(ValueError, match="qualified human"):
        repository.record_qualified_mention(
            "observation:bot", "game:genshin-impact", "scene:1"
        )
    assert repository.topic_affinity(
        "g1", "game:genshin-impact", now=100
    ) is None
