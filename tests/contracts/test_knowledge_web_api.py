from __future__ import annotations

import asyncio
import json
import time

from groupmate.adapters.web_api import ControlPlaneWebAPI, WebRequest
from groupmate.social_runtime.control.commands import CommandService
from groupmate.social_runtime.control.knowledge import KnowledgeControlQueries
from groupmate.social_runtime.control.projections import ProjectionConsumer
from groupmate.social_runtime.control.queries import ProjectionQueries
from groupmate.social_runtime.control.stream import ProjectionStream
from groupmate.social_runtime.knowledge.repository import KnowledgeRepository
from groupmate.social_runtime.persistence.schema import (
    connect_database,
    initialize_database,
)


def _seed(path) -> None:
    initialize_database(path)
    ProjectionConsumer(path, "runtime")
    with connect_database(path) as db:
        db.execute(
            "INSERT INTO knowledge_entities(entity_id,entity_type,canonical_name,"
            "canonical_game_id,status,created_at,updated_at) VALUES("
            "'game:delta','game','三角洲行动','game:delta','active',1,1)"
        )
        db.execute(
            "INSERT INTO group_topic_affinity(group_id,entity_id,"
            "qualified_mention_count,distinct_actor_count,distinct_scene_count,"
            "salience,first_seen_at,last_seen_at,updated_at) VALUES("
            "'group-1','game:delta',12,4,4,0.8,1,20,20)"
        )
        db.execute(
            "INSERT INTO group_conventions(convention_id,group_id,"
            "normalized_expression,resolved_entity_id,meaning_summary,"
            "evidence_observation_ids_json,distinct_actor_count,distinct_scene_count,"
            "confidence,status,first_seen_at,last_seen_at,updated_at) VALUES("
            "'convention:delta','group-1','洲','game:delta','指三角洲行动',"
            "'[]',3,2,0.7,'candidate',1,20,20)"
        )
        db.execute(
            "INSERT INTO game_release_states(version_slot_id,game_entity_id,"
            "official_label,region,platform,release_state,official_state,rumor_state,"
            "release_checked_at,official_checked_at,rumor_checked_at,fresh_until,"
            "status,revision) VALUES('slot:delta:current','game:delta','S7',"
            "'CN','all','current','released','none_observed',100,110,90,200,"
            "'active',3)"
        )
        db.execute(
            "INSERT INTO knowledge_claims(claim_id,subject_entity_id,predicate,"
            "safe_summary,claim_kind,evidence_level,status,"
            "applies_to_version_slot_id,region,platform,valid_from,valid_until,"
            "checked_at,created_at,updated_at) VALUES("
            "'claim:s7','game:delta','current_version','S7 已正式发布',"
            "'public_fact','official','active','slot:delta:current','CN','all',"
            "80,200,110,100,110)"
        )
        db.execute(
            "INSERT INTO knowledge_sources(source_id,canonical_url,domain,publisher,"
            "source_class,published_at,fetched_at,content_hash,evidence_excerpt) "
            "VALUES('source:s7','https://official.example/news/s7?utm_source=private#details',"
            "'official.example','Delta Studio','official',90,100,"
            "'private-content-hash','官方公告确认 S7 已正式发布。')"
        )
        db.execute(
            "INSERT INTO knowledge_claim_evidence(evidence_id,claim_id,source_id,"
            "observation_id,relation_kind,created_at) VALUES("
            "'evidence:s7','claim:s7','source:s7',NULL,'supports',110)"
        )
        db.execute(
            "INSERT INTO group_knowledge_aliases(alias_id,group_id,entity_id,"
            "normalized_alias,evidence_observation_ids_json,confidence,last_used_at,status) "
            "VALUES('alias:delta','group-1','game:delta','洲',"
            "'[\"private-observation-id\"]',0.9,120,'active')"
        )
        db.execute(
            "INSERT INTO knowledge_usage(usage_id,group_id,source_event_id,"
            "knowledge_ids_json,source_domains_json,query_intent_hash,latency_ms,"
            "cache_hit,result_kind,diagnostic_code,recorded_at) VALUES("
            "'usage:1','group-1',NULL,'[\"claim:safe\"]','[\"official.example\"]',"
            "'private-intent-hash',25,0,'grounded_reply',NULL,120)"
        )
    KnowledgeRepository(path).record_runtime_metric(
        metric_kind="local_resolution",
        latency_ms=6,
        diagnostic_code=None,
        now=int(time.time()),
    )


def _api(path, published):
    service = CommandService(
        path,
        persona_id="aemeath",
        group_ids=("group-1",),
        admin_ids=("admin:root",),
        clock=lambda: 150,
    )
    return ControlPlaneWebAPI(
        queries=ProjectionQueries(path),
        knowledge_queries=KnowledgeControlQueries(path, persona_id="aemeath"),
        stream=ProjectionStream(path),
        command_service_for=lambda _username: service,
        event_publisher=published.append,
        persona_id="aemeath",
        group_ids=("group-1",),
        admin_ids=("admin:root",),
    )


def _request(path, *, method="GET", query=None, body=None, username="admin:root", headers=None):
    return WebRequest(
        method=method,
        path=path,
        query={"group_id": "group-1"} if query is None else query,
        headers=headers or {},
        json_body=body,
        username=username,
    )


def test_knowledge_queries_are_scoped_safe_uncached_and_validate_cursor(tmp_path):
    path = tmp_path / "runtime.db"
    _seed(path)
    api = _api(path, [])

    library = asyncio.run(
        api.handle(_request("/knowledge/library/overview", query={}))
    )
    group = asyncio.run(api.handle(_request("/knowledge/group/overview")))
    entities = asyncio.run(
        api.handle(_request("/knowledge/library/entities", query={}))
    )
    invalid_cursor = asyncio.run(
        api.handle(
            _request(
                "/knowledge/library/claims",
                query={"cursor": "not-a-cursor"},
            )
        )
    )
    missing_group = asyncio.run(
        api.handle(_request("/knowledge/group/jobs", query={}))
    )
    other_group = asyncio.run(
        api.handle(
            _request("/knowledge/group/conventions", query={"group_id": "group-2"})
        )
    )

    assert library.status == group.status == entities.status == 200
    assert library.body["release_states"][0]["official_label"] == "S7"
    assert library.body["operations"]["local_resolution"] == {
        "count": 1,
        "p95_ms": 6,
    }
    assert library.body["operations"]["window_seconds"] == 86_400
    assert group.body["recent_usage"][0]["result_kind"] == "grounded_reply"
    assert library.body["scope"] == {"kind": "library"}
    assert group.body["scope"] == {"kind": "group", "group_id": "group-1"}
    assert "group_id" not in library.body
    assert library.headers == {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
    serialized = json.dumps(group.body, ensure_ascii=False)
    assert "private-intent-hash" not in serialized
    assert invalid_cursor.status == missing_group.status == 400
    assert other_group.status == 404


def test_knowledge_actions_enforce_auth_content_size_and_revision(tmp_path):
    path = tmp_path / "runtime.db"
    _seed(path)
    published = []
    api = _api(path, published)
    body = {
        "type": "knowledge_convention_confirm",
        "command_id": "knowledge:web:confirm",
        "expected_version": 0,
        "reason": "群内用法已人工复核",
        "confirmed": False,
        "group_id": "group-1",
        "payload": {"convention_id": "convention:delta"},
    }

    unauthorized = asyncio.run(
        api.handle(
            _request(
                "/knowledge/group/actions",
                method="POST",
                body=body,
                username="member:1",
                headers={"Content-Type": "application/json"},
            )
        )
    )
    wrong_type = asyncio.run(
        api.handle(
            _request(
                "/knowledge/group/actions",
                method="POST",
                body=body,
                headers={"Content-Type": "text/plain"},
            )
        )
    )
    oversized = asyncio.run(
        api.handle(
            _request(
                "/knowledge/group/actions",
                method="POST",
                body={**body, "reason": "x" * 20_000},
                headers={"Content-Type": "application/json"},
            )
        )
    )
    accepted = asyncio.run(
        api.handle(
            _request(
                "/knowledge/group/actions",
                method="POST",
                body=body,
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
        )
    )
    stale = asyncio.run(
        api.handle(
            _request(
                "/knowledge/group/actions",
                method="POST",
                body={
                    **body,
                    "type": "knowledge_convention_reject",
                    "command_id": "knowledge:web:stale",
                },
                headers={"Content-Type": "application/json"},
            )
        )
    )

    assert unauthorized.status == 403
    assert wrong_type.status == 415
    assert oversized.status == 413
    assert accepted.status == 202
    assert accepted.body == {
        "accepted": True,
        "command_id": "knowledge:web:confirm",
        "action_ref": accepted.body["action_ref"],
        "version": 1,
    }
    assert stale.status == 409
    assert stale.body["current_version"] == 1
    assert published[0].event_type == "control.knowledge.convention_confirmed"


def test_library_detail_and_group_context_stay_separate_and_private(
    tmp_path,
):
    """Catches public-fact isolation, group-overlay leakage, or unsafe evidence."""
    path = tmp_path / "runtime.db"
    _seed(path)
    api = _api(path, [])

    library = asyncio.run(
        api.handle(
            _request(
                "/knowledge/library/entity-detail",
                query={"entity_id": "game:delta"},
            )
        )
    )
    group = asyncio.run(
        api.handle(
            _request(
                "/knowledge/group/entity-context",
                query={"group_id": "group-1", "entity_id": "game:delta"},
            )
        )
    )
    forbidden_scope = asyncio.run(
        api.handle(
            _request(
                "/knowledge/group/entity-context",
                query={"group_id": "group-2", "entity_id": "game:delta"},
            )
        )
    )
    missing = asyncio.run(
        api.handle(
            _request(
                "/knowledge/library/entity-detail",
                query={"entity_id": "game:missing"},
            )
        )
    )

    assert library.status == group.status == 200
    assert library.body["scope"] == {"kind": "library"}
    assert library.body["entity"] == {
        "entity_id": "game:delta",
        "entity_type": "game",
        "canonical_name": "三角洲行动",
        "canonical_game_id": "game:delta",
        "canonical_game_name": "三角洲行动",
        "status": "active",
    }
    assert library.body["related_entities"] == []
    assert "group_aliases" not in library.body
    assert "group_conventions" not in library.body
    assert group.body["scope"] == {"kind": "group", "group_id": "group-1"}
    assert group.body["entity_id"] == "game:delta"
    assert group.body["group_aliases"] == [
        {
            "expression": "洲",
            "confidence": 0.9,
            "last_used_at": 120,
            "status": "active",
        }
    ]
    assert "claims" not in group.body
    assert "release_states" not in group.body
    assert library.body["claims"][0]["safe_summary"] == "S7 已正式发布"
    assert library.body["claims"][0]["sources"] == [
        {
            "publisher": "Delta Studio",
            "domain": "official.example",
            "source_class": "official",
            "url": "https://official.example/news/s7",
            "published_at": 90,
            "fetched_at": 100,
            "excerpt": "官方公告确认 S7 已正式发布。",
            "relation_kind": "supports",
        }
    ]
    serialized = json.dumps({"library": library.body, "group": group.body}, ensure_ascii=False)
    assert "private-content-hash" not in serialized
    assert "private-observation-id" not in serialized
    assert "utm_source" not in serialized
    assert forbidden_scope.status == missing.status == 404
