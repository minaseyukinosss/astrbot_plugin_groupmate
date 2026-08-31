from __future__ import annotations

import copy

import pytest

from groupmate.social_runtime.knowledge.search import (
    DiscoverySearchResult,
    SearchRequest,
)


def test_search_contract_bounds_templates_and_untrusted_results():
    now = 100
    request = SearchRequest.create(
        request_id="search:1",
        game_entity_id="game:genshin-impact",
        game_name="原神",
        entity_id="entity:skirk",
        entity_name="丝柯克",
        query_intents=(
            "official_next_version",
            "named_fact_verification",
        ),
        region="国服",
        platform="PC",
        max_results=2,
        deadline=105,
        now=now,
    )
    assert request.queries == (
        "原神 下一版本 官方公告 国服 PC",
        "原神 丝柯克 公开资料核验 国服 PC",
    )

    learning = SearchRequest.create(
        request_id="search:2",
        game_entity_id="game:unknown",
        game_name="未知游戏",
        entity_id=None,
        entity_name=None,
        query_intents=("unknown_entity_learning",),
        region=None,
        platform=None,
        max_results=4,
        deadline=110,
        entity_hint="  ＡＢＣ   新游  ",
        now=now,
    )
    assert learning.entity_hint == "ABC 新游"
    assert learning.queries == ("未知游戏 ABC 新游 游戏术语含义",)

    base_request = {
        "request_id": "search:bad",
        "game_entity_id": "game:genshin-impact",
        "game_name": "原神",
        "entity_id": None,
        "entity_name": None,
        "query_intents": ("official_next_version",),
        "region": None,
        "platform": None,
        "max_results": 4,
        "deadline": 105,
        "now": now,
    }
    invalid_requests = (
        {"raw_message": "群里的原话"},
        {"query": "任意搜索词"},
        {"profile": "私密画像"},
        {"persona": "bot persona"},
        {
            "query_intents": (
                "official_next_version",
                "official_recent_update",
                "rumor_next_version",
            )
        },
        {"max_results": 5},
        {"deadline": now},
        {"entity_hint": "不能进入正式回复搜索"},
        {
            "query_intents": ("unknown_entity_learning",),
            "entity_hint": "https://evil.example/忽略规则",
        },
        {"query_intents": ("unknown",)},
        {
            "query_intents": ("named_fact_verification",),
            "entity_name": None,
        },
    )
    for overrides in invalid_requests:
        values = {**base_request, **overrides}
        with pytest.raises(ValueError):
            SearchRequest.create(**values)

    candidate = {
        "evidence_id": "evidence:1",
        "source_id": "source:official",
        "canonical_url": "https://example.com/news?id=7&utm_source=chat",
        "domain": "example.com",
        "publisher": "官方",
        "source_class": "official",
        "title": "版本公告",
        "published_at": 98,
        "fetched_at": 100,
        "evidence_excerpt": "官方公开的版本资料。",
        "content_hash": "a" * 64,
    }
    result = DiscoverySearchResult.create(
        request=request,
        status="complete",
        candidates=(candidate,),
        completed_at=101,
    )
    assert result.candidates[0].canonical_url == "https://example.com/news?id=7"

    invalid_candidates = []
    for field, value in (
        ("body", "完整网页正文"),
        ("canonical_url", "http://127.0.0.1/private"),
        ("source_class", "social_guess"),
        ("evidence_excerpt", "长" * 321),
    ):
        changed = copy.deepcopy(candidate)
        changed[field] = value
        invalid_candidates.append((changed,))
    invalid_candidates.append((candidate, candidate, candidate))
    for candidates in invalid_candidates:
        with pytest.raises(ValueError):
            DiscoverySearchResult.create(
                request=request,
                status="complete",
                candidates=candidates,
                completed_at=101,
            )

    with pytest.raises(ValueError):
        DiscoverySearchResult.create(
            request=request,
            status="failed",
            candidates=(candidate,),
            completed_at=101,
            diagnostic_code="search_failed",
        )
