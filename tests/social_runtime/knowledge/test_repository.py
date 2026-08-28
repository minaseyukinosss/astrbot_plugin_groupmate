from __future__ import annotations

import importlib

import pytest

from groupmate.social_runtime.knowledge.contracts import KnowledgeObservation


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
