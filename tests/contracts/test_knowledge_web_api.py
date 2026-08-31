from __future__ import annotations

import asyncio
import json

from groupmate.adapters.web_api import ControlPlaneWebAPI, WebRequest
from groupmate.social_runtime.control.commands import CommandService
from groupmate.social_runtime.control.knowledge import KnowledgeControlQueries
from groupmate.social_runtime.control.projections import ProjectionConsumer
from groupmate.social_runtime.control.queries import ProjectionQueries
from groupmate.social_runtime.control.stream import ProjectionStream
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
            "INSERT INTO knowledge_usage(usage_id,group_id,source_event_id,"
            "knowledge_ids_json,source_domains_json,query_intent_hash,latency_ms,"
            "cache_hit,result_kind,diagnostic_code,recorded_at) VALUES("
            "'usage:1','group-1',NULL,'[\"claim:safe\"]','[\"official.example\"]',"
            "'private-intent-hash',25,0,'grounded_reply',NULL,120)"
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
        query=query or {"group_id": "group-1"},
        headers=headers or {},
        json_body=body,
        username=username,
    )


def test_knowledge_queries_are_scoped_safe_uncached_and_validate_cursor(tmp_path):
    path = tmp_path / "runtime.db"
    _seed(path)
    api = _api(path, [])

    overview = asyncio.run(api.handle(_request("/knowledge/overview")))
    entities = asyncio.run(api.handle(_request("/knowledge/entities")))
    invalid_cursor = asyncio.run(
        api.handle(
            _request(
                "/knowledge/claims",
                query={"group_id": "group-1", "cursor": "not-a-cursor"},
            )
        )
    )
    missing_group = asyncio.run(
        api.handle(_request("/knowledge/jobs", query={"persona_id": "aemeath"}))
    )
    other_group = asyncio.run(
        api.handle(_request("/knowledge/conventions", query={"group_id": "group-2"}))
    )

    assert overview.status == entities.status == 200
    assert overview.body["release_states"][0]["official_label"] == "S7"
    assert overview.body["recent_usage"][0]["result_kind"] == "grounded_reply"
    assert overview.body["scope"] == {
        "persona_id": "aemeath",
        "group_id": "group-1",
    }
    assert overview.headers == {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
    serialized = json.dumps(overview.body, ensure_ascii=False)
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
                "/knowledge/actions",
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
                "/knowledge/actions",
                method="POST",
                body=body,
                headers={"Content-Type": "text/plain"},
            )
        )
    )
    oversized = asyncio.run(
        api.handle(
            _request(
                "/knowledge/actions",
                method="POST",
                body={**body, "reason": "x" * 20_000},
                headers={"Content-Type": "application/json"},
            )
        )
    )
    accepted = asyncio.run(
        api.handle(
            _request(
                "/knowledge/actions",
                method="POST",
                body=body,
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
        )
    )
    stale = asyncio.run(
        api.handle(
            _request(
                "/knowledge/actions",
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
