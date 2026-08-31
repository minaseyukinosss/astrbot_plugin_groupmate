from __future__ import annotations

import copy
import hashlib
import importlib
import json
from urllib.parse import urlsplit

import pytest

from groupmate.social_runtime.knowledge.repository import KnowledgeRepository
from groupmate.social_runtime.persistence.schema import connect_database


EXPECTED_GAMES = {
    "game:delta-force": "三角洲行动",
    "game:wuthering-waves": "鸣潮",
    "game:honkai-star-rail": "崩坏：星穹铁道",
    "game:zenless-zone-zero": "绝区零",
}


def _seeds_module():
    return importlib.import_module("groupmate.social_runtime.knowledge.seeds")


def _canonical_hash(payload):
    canonical = dict(payload)
    canonical.pop("content_hash", None)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _write_manifest(path, payload):
    value = copy.deepcopy(payload)
    value["content_hash"] = _canonical_hash(value)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _minimal_manifest():
    return {
        "seed_id": "game-semantic:test-game",
        "seed_version": 1,
        "game": {
            "entity_id": "game:test-game",
            "canonical_name": "测试游戏",
            "english_name": "Test Game",
            "aliases": [
                {
                    "text": "测试游戏",
                    "kind": "official",
                    "ambiguity_level": "none",
                    "requires_any_context": [],
                }
            ],
        },
        "entity_types": [
            "game",
            "character",
            "mode",
            "resource",
            "mechanic",
        ],
        "entities": [],
        "terms": [
            {
                "term_id": f"term:test:{index}",
                "text": f"术语{index}",
                "meaning_summary": f"稳定语义{index}",
                "term_kind": "community",
                "ambiguity_level": "none",
                "requires_any_context": [],
            }
            for index in range(12)
        ],
        "stable_relations": [
            {
                "relation_id": f"relation:test:{index}",
                "subject_entity_id": "game:test-game",
                "predicate": "uses_term",
                "object_entity_id": f"term:test:{index}",
                "safe_summary": f"测试游戏使用术语{index}",
            }
            for index in range(3)
        ],
        "discussion_patterns": [f"讨论模式{index}" for index in range(8)],
        "official_sources": [
            {
                "source_id": "source:test:official-news",
                "publisher": "测试游戏官方",
                "domain": "game.example.com",
                "url": "https://game.example.com/news",
                "required": True,
            }
        ],
    }


def test_bundled_seeds_cover_four_games_with_stable_semantics_only():
    module = _seeds_module()
    seeds = module.load_bundled_seeds()

    assert {seed.game.entity_id: seed.game.canonical_name for seed in seeds} == (
        EXPECTED_GAMES
    )
    assert len({seed.seed_id for seed in seeds}) == 4
    for seed in seeds:
        assert seed.seed_version == 1
        assert len(seed.entity_types) >= 5
        assert len(seed.terms) + len(seed.discussion_patterns) >= 20
        assert len(seed.stable_relations) >= 3
        assert seed.game.english_name
        assert seed.official_sources
        for source in seed.official_sources:
            parsed = urlsplit(source.url)
            assert parsed.scheme == "https"
            assert parsed.hostname == source.domain
        assert seed.content_hash == _canonical_hash(seed.manifest)


def test_import_retires_former_genshin_bundle_but_preserves_group_learning(
    tmp_path,
):
    module = _seeds_module()
    repository = KnowledgeRepository(
        tmp_path / "groupmate-social-runtime-v2.db"
    )
    legacy = _minimal_manifest()
    legacy["seed_id"] = "game-semantic:genshin-impact"
    legacy["game"].update(
        {
            "entity_id": "game:genshin-impact",
            "canonical_name": "原神",
            "english_name": "Genshin Impact",
            "aliases": [
                {
                    "text": "原神",
                    "kind": "official",
                    "ambiguity_level": "none",
                    "requires_any_context": [],
                }
            ],
        }
    )
    for relation in legacy["stable_relations"]:
        relation["subject_entity_id"] = "game:genshin-impact"
    legacy["content_hash"] = _canonical_hash(legacy)
    repository.import_seed_manifest(legacy, imported_at=10)
    repository.put_group_alias(
        alias_id="group-alias:genshin",
        group_id="g1",
        entity_id="game:genshin-impact",
        normalized_alias="原神",
        evidence_observation_ids=("observation:group-learned",),
        confidence=0.95,
        last_used_at=11,
        status="active",
    )
    with connect_database(repository.path) as db:
        db.execute(
            "INSERT INTO knowledge_jobs(job_id,idempotency_key,job_kind,"
            "group_id,entity_id,request_json,status,attempt,next_attempt_at,"
            "diagnostic_code,created_at,updated_at) VALUES("
            "'job:genshin','daily:genshin','official_daily_probe',NULL,"
            "'game:genshin-impact','{}','pending',0,20,NULL,10,10)"
        )

    module.SeedImporter(repository, clock=lambda: 20).import_all(
        module.load_bundled_seeds()
    )

    assert [
        item.status
        for item in repository.seed_versions(
            "game-semantic:genshin-impact"
        )
    ] == ["superseded"]
    assert repository.game_alias_matches("原神新版本") == ()
    assert [
        item.entity_id for item in repository.active_group_aliases("g1")
    ] == ["game:genshin-impact"]
    assert [
        item.status
        for item in repository.knowledge_jobs()
        if item.entity_id == "game:genshin-impact"
    ] == ["discarded"]
    with connect_database(repository.path) as db:
        active_bundled = db.execute(
            "SELECT COUNT(*) FROM knowledge_claims WHERE claim_id LIKE "
            "'claim:seed:game-semantic:genshin-impact:%' AND status='active'"
        ).fetchone()[0]
    assert active_bundled == 0


@pytest.mark.parametrize(
    "banned_key",
    [
        "current_banner",
        "current_version",
        "latest_numbers",
        "tier_list",
        "leak_content",
    ],
)
def test_seed_loader_rejects_temporal_or_recommendation_fields(
    tmp_path, banned_key
):
    module = _seeds_module()
    payload = _minimal_manifest()
    payload["game"][banned_key] = "不应进入稳定 seed"
    path = _write_manifest(tmp_path / "invalid.json", payload)

    with pytest.raises(module.SeedValidationError, match=banned_key):
        module.load_seed_asset(path)


def test_seed_loader_rejects_non_https_or_mismatched_official_domain(tmp_path):
    module = _seeds_module()
    payload = _minimal_manifest()
    payload["official_sources"][0]["url"] = "http://mirror.example.net/news"
    path = _write_manifest(tmp_path / "invalid-source.json", payload)

    with pytest.raises(module.SeedValidationError, match="official source"):
        module.load_seed_asset(path)


def test_high_ambiguity_alias_requires_context_terms(tmp_path):
    module = _seeds_module()
    payload = _minimal_manifest()
    payload["game"]["aliases"].append(
        {
            "text": "测试",
            "kind": "abbreviation",
            "ambiguity_level": "high",
            "requires_any_context": [],
        }
    )
    path = _write_manifest(tmp_path / "ambiguous.json", payload)

    with pytest.raises(module.SeedValidationError, match="requires_any_context"):
        module.load_seed_asset(path)


def test_seed_import_is_idempotent_and_detects_same_version_hash_conflict(
    tmp_path,
):
    module = _seeds_module()
    repository = KnowledgeRepository(
        tmp_path / "groupmate-social-runtime-v2.db"
    )
    seed = module.load_bundled_seeds()[0]
    importer = module.SeedImporter(repository, clock=lambda: 100)

    first = importer.import_all((seed,))
    second = importer.import_all((seed,))

    assert first.imported_seed_ids == (seed.seed_id,)
    assert second.unchanged_seed_ids == (seed.seed_id,)

    conflicting = copy.deepcopy(seed.manifest)
    conflicting["discussion_patterns"].append("新的稳定讨论模式")
    conflict_path = _write_manifest(tmp_path / "conflict.json", conflicting)
    conflict_seed = module.load_seed_asset(conflict_path)

    with pytest.raises(module.SeedHashConflict, match=seed.seed_id):
        importer.import_all((conflict_seed,))


def test_new_seed_version_supersedes_only_older_bundled_material(tmp_path):
    module = _seeds_module()
    repository = KnowledgeRepository(
        tmp_path / "groupmate-social-runtime-v2.db"
    )
    seed = module.load_bundled_seeds()[0]
    importer = module.SeedImporter(repository, clock=lambda: 100)
    importer.import_all((seed,))
    with connect_database(repository.path) as db:
        db.execute(
            "INSERT INTO knowledge_claims("
            "claim_id,subject_entity_id,predicate,safe_summary,claim_kind,"
            "evidence_level,status,created_at,updated_at) VALUES("
            "'claim:official-preserved',?,'official_identity','官方验证事实',"
            "'public_fact','official','active',100,100)",
            (seed.game.entity_id,),
        )
        db.execute(
            "UPDATE knowledge_sources SET fetched_at=200,"
            "content_hash='official-fetch-hash',"
            "evidence_excerpt='官网抓取后的证据摘要' WHERE source_id=?",
            (seed.official_sources[0].source_id,),
        )

    upgraded = copy.deepcopy(seed.manifest)
    upgraded["seed_version"] = 2
    upgraded["discussion_patterns"].append("版本无关的新讨论模式")
    upgraded_path = _write_manifest(tmp_path / "upgraded.json", upgraded)
    importer.import_all((module.load_seed_asset(upgraded_path),))

    assert [
        (item.seed_version, item.status)
        for item in repository.seed_versions(seed.seed_id)
    ] == [(1, "superseded"), (2, "active")]
    with connect_database(repository.path) as db:
        official_status = db.execute(
            "SELECT status FROM knowledge_claims "
            "WHERE claim_id='claim:official-preserved'"
        ).fetchone()[0]
        official_source = db.execute(
            "SELECT fetched_at,content_hash,evidence_excerpt "
            "FROM knowledge_sources WHERE source_id=?",
            (seed.official_sources[0].source_id,),
        ).fetchone()
    assert official_status == "active"
    assert tuple(official_source) == (
        200,
        "official-fetch-hash",
        "官网抓取后的证据摘要",
    )


def test_import_rejects_a_seed_older_than_the_active_version(tmp_path):
    module = _seeds_module()
    repository = KnowledgeRepository(
        tmp_path / "groupmate-social-runtime-v2.db"
    )
    seed = module.load_bundled_seeds()[0]
    importer = module.SeedImporter(repository, clock=lambda: 100)

    newest = copy.deepcopy(seed.manifest)
    newest["seed_version"] = 3
    newest["discussion_patterns"].append("第三版稳定模式")
    newest_path = _write_manifest(tmp_path / "newest.json", newest)
    importer.import_all((module.load_seed_asset(newest_path),))

    older = copy.deepcopy(seed.manifest)
    older["seed_version"] = 2
    older["discussion_patterns"].append("第二版稳定模式")
    older_path = _write_manifest(tmp_path / "older.json", older)

    with pytest.raises(module.SeedVersionOrderError, match="newer"):
        importer.import_all((module.load_seed_asset(older_path),))
