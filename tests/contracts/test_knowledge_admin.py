from __future__ import annotations

import json

import pytest

from groupmate.social_runtime.control.commands import (
    CommandContext,
    CommandForbidden,
    CommandNotFound,
    CommandService,
    ConfirmKnowledgeConvention,
    DisputeKnowledgeClaim,
    ExpectedVersionConflict,
    InvalidateKnowledgeCache,
    RejectKnowledgeConvention,
    RetryKnowledgeJob,
    SetKnowledgeAmbientCanary,
    SupersedeKnowledgeAlias,
)
from groupmate.social_runtime.control.knowledge import KnowledgeControlQueries
from groupmate.social_runtime.persistence.schema import (
    connect_database,
    initialize_database,
)


def _context(*, group_id="group-1", admin_id="admin:root", version=0):
    return CommandContext(
        admin_id=admin_id,
        persona_id="aemeath",
        group_id=group_id,
        expected_version=version,
        reason="reviewed by operator",
        confirmed=True,
    )


def _service(path, *, now=1_800_000_000):
    return CommandService(
        path,
        persona_id="aemeath",
        group_ids=("group-1", "group-2"),
        admin_ids=("admin:root",),
        clock=lambda: now,
    )


def _seed_knowledge(path) -> None:
    initialize_database(path)
    with connect_database(path) as db:
        db.execute(
            "INSERT INTO knowledge_entities(entity_id,entity_type,canonical_name,"
            "canonical_game_id,status,created_at,updated_at) "
            "VALUES('game:delta','game','三角洲行动','game:delta','active',10,20),"
            "('game:wuthering','game','鸣潮','game:wuthering','active',10,20)"
        )
        db.execute(
            "INSERT INTO group_topic_affinity(group_id,entity_id,"
            "qualified_mention_count,distinct_actor_count,distinct_scene_count,"
            "salience,first_seen_at,last_seen_at,updated_at) "
            "VALUES('group-1','game:delta',12,5,4,0.9,10,90,100)"
        )
        db.execute(
            "INSERT INTO knowledge_sources(source_id,canonical_url,domain,publisher,"
            "source_class,published_at,fetched_at,content_hash,evidence_excerpt) "
            "VALUES('source:official','https://secret.invalid/path?token=raw-query',"
            "'secret.invalid','官方站','official',30,40,'hash','raw excerpt secret')"
        )
        db.execute(
            "INSERT INTO knowledge_claims(claim_id,subject_entity_id,predicate,"
            "safe_summary,claim_kind,evidence_level,status,checked_at,created_at,updated_at) "
            "VALUES('claim:mode','game:delta','genre','多人战术射击',"
            "'stable_semantic','official','active',50,10,60)"
        )
        db.execute(
            "INSERT INTO knowledge_claim_evidence(evidence_id,claim_id,source_id,"
            "relation_kind,created_at) VALUES('evidence:mode','claim:mode',"
            "'source:official','supports',60)"
        )
        db.execute(
            "INSERT INTO knowledge_observations(observation_id,origin_class,scope_kind,"
            "group_id,author_ref,source_event_id,source_id,entity_hint,safe_summary,"
            "content_hash,occurred_at,recorded_at,status) VALUES("
            "'observation:chat','human_chat','group','group-1','author:private',"
            "'event:private','scene:private','三角洲','raw event text secret',"
            "'observation-hash',70,70,'admitted')"
        )
        db.execute(
            "INSERT INTO group_conventions(convention_id,group_id,"
            "normalized_expression,resolved_entity_id,meaning_summary,"
            "evidence_observation_ids_json,distinct_actor_count,distinct_scene_count,"
            "confidence,status,first_seen_at,last_seen_at,updated_at) VALUES("
            "'convention:delta','group-1','洲','game:delta','指三角洲行动',"
            "'[\"observation:chat\"]',3,2,0.7,'candidate',70,90,100),"
            "('convention:other','group-2','洲','game:delta','另一个群的约定',"
            "'[]',3,2,0.7,'candidate',70,90,100)"
        )
        db.execute(
            "INSERT INTO group_knowledge_aliases(alias_id,group_id,entity_id,"
            "normalized_alias,evidence_observation_ids_json,confidence,last_used_at,status) "
            "VALUES('alias:old','group-1','game:delta','老洲','[]',0.8,90,'active')"
        )
        db.execute(
            "INSERT INTO knowledge_aliases(alias_id,entity_id,normalized_alias,"
            "alias_kind,ambiguity_level,source_id,status) VALUES("
            "'alias:global','game:delta','三角洲','official','none',"
            "'source:official','active')"
        )
        db.execute(
            "INSERT INTO knowledge_jobs(job_id,idempotency_key,job_kind,group_id,"
            "entity_id,request_json,status,attempt,next_attempt_at,diagnostic_code,"
            "created_at,updated_at) VALUES('job:retry','job-key',"
            "'unknown_entity_learning','group-1','game:delta',"
            "'{\"query\":\"full private search query\"}','retry',2,9999999999,"
            "'source_unavailable',80,90)"
        )
        db.execute(
            "INSERT INTO negative_search_snapshots(snapshot_id,game_entity_id,"
            "query_intent,covered_source_ids_json,checked_at,expires_at,"
            "version_state_revision,status,diagnostic_code) VALUES("
            "'snapshot:delta','game:delta','next_version','[]',80,9999999999,"
            "1,'active','no_official_info')"
        )


def test_knowledge_queries_return_safe_group_scoped_projections(tmp_path):
    path = tmp_path / "runtime.db"
    _seed_knowledge(path)
    queries = KnowledgeControlQueries(path, persona_id="aemeath")

    result = {
        "overview": queries.overview("group-1", now=200),
        "entities": queries.entities("group-1", None, {}),
        "claims": queries.claims("group-1", None, {}),
        "conventions": queries.conventions("group-1", None, {}),
        "jobs": queries.jobs("group-1", None, {}),
    }

    assert result["overview"]["popular_games"][0]["canonical_name"] == "三角洲行动"
    assert result["claims"]["items"][0]["sources"] == [
        {"domain": "secret.invalid", "source_class": "official"}
    ]
    assert result["conventions"]["items"][0]["scope"] == "group"
    assert result["jobs"]["items"][0]["diagnostic"] == "source_unavailable"
    serialized = json.dumps(result, ensure_ascii=False)
    for forbidden in (
        "token=raw-query",
        "raw excerpt secret",
        "full private search query",
        "author:private",
        "event:private",
        "raw event text secret",
        "scene:private",
    ):
        assert forbidden not in serialized


def test_convention_admin_enforces_authority_scope_revision_and_idempotency(tmp_path):
    path = tmp_path / "runtime.db"
    _seed_knowledge(path)
    service = _service(path)
    command = ConfirmKnowledgeConvention(
        "convention:delta", command_id="cmd:confirm"
    )

    with pytest.raises(CommandForbidden):
        service.execute(command, _context(admin_id="member:1"))
    with pytest.raises(CommandNotFound):
        service.execute(
            ConfirmKnowledgeConvention("convention:other"), _context()
        )

    first = service.execute(command, _context())
    assert service.execute(command, _context()) == first
    with pytest.raises(ExpectedVersionConflict):
        service.execute(
            RejectKnowledgeConvention("convention:delta"), _context(version=0)
        )
    service.execute(
        ConfirmKnowledgeConvention(
            "convention:delta", command_id="cmd:confirm-again"
        ),
        _context(version=1),
    )

    with connect_database(path) as db:
        convention = db.execute(
            "SELECT status FROM group_conventions WHERE convention_id='convention:delta'"
        ).fetchone()
        group_alias = db.execute(
            "SELECT status FROM group_knowledge_aliases "
            "WHERE group_id='group-1' AND normalized_alias='洲'"
        ).fetchone()
        global_alias_count = db.execute(
            "SELECT COUNT(*) FROM knowledge_aliases WHERE normalized_alias='洲'"
        ).fetchone()[0]
        admin_observations = db.execute(
            "SELECT COUNT(*) FROM knowledge_observations WHERE origin_class='admin'"
        ).fetchone()[0]
    assert convention[0] == "active"
    assert group_alias[0] == "active"
    assert global_alias_count == 0
    assert admin_observations == 2


def test_remaining_admin_mutations_are_audited_and_preserve_history(tmp_path):
    path = tmp_path / "runtime.db"
    _seed_knowledge(path)
    service = _service(path)

    commands = (
        SupersedeKnowledgeAlias(
            "alias:old", "game:wuthering", command_id="cmd:supersede"
        ),
        DisputeKnowledgeClaim("claim:mode", command_id="cmd:dispute"),
        RetryKnowledgeJob("job:retry", command_id="cmd:retry"),
        InvalidateKnowledgeCache("game:delta", command_id="cmd:invalidate"),
        SetKnowledgeAmbientCanary(True, command_id="cmd:canary"),
    )
    for version, command in enumerate(commands):
        result = service.execute(command, _context(version=version))
        assert result.version == version + 1

    with connect_database(path) as db:
        old_alias = db.execute(
            "SELECT status FROM group_knowledge_aliases WHERE alias_id='alias:old'"
        ).fetchone()[0]
        replacement = db.execute(
            "SELECT entity_id,status FROM group_knowledge_aliases "
            "WHERE group_id='group-1' AND normalized_alias='老洲' "
            "AND entity_id='game:wuthering'"
        ).fetchone()
        claim = db.execute(
            "SELECT status FROM knowledge_claims WHERE claim_id='claim:mode'"
        ).fetchone()[0]
        job = db.execute(
            "SELECT status,next_attempt_at FROM knowledge_jobs WHERE job_id='job:retry'"
        ).fetchone()
        snapshot = db.execute(
            "SELECT status FROM negative_search_snapshots "
            "WHERE snapshot_id='snapshot:delta'"
        ).fetchone()[0]
        action_types = {
            row[0]
            for row in db.execute(
                "SELECT action_type FROM governance_actions ORDER BY created_at"
            ).fetchall()
        }
        observation_count = db.execute(
            "SELECT COUNT(*) FROM knowledge_observations WHERE origin_class='admin'"
        ).fetchone()[0]
    assert old_alias == "stale"
    assert tuple(replacement) == ("game:wuthering", "active")
    assert claim == "disputed"
    assert tuple(job) == ("retry", 1_800_000_000)
    assert snapshot == "invalidated"
    assert "knowledge.ambient_canary_enabled" in action_types
    assert observation_count == len(commands)

    overview = KnowledgeControlQueries(path, persona_id="aemeath").overview(
        "group-1", now=1_800_000_001
    )
    assert overview["ambient_canary_enabled"] is True
