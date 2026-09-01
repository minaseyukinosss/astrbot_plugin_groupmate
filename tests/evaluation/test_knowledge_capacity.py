from __future__ import annotations

import importlib

import pytest

from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.knowledge.repository import KnowledgeRepository
from groupmate.social_runtime.knowledge.resolver import KnowledgeEntityResolver
from groupmate.social_runtime.knowledge.seeds import SeedImporter, load_bundled_seeds
from groupmate.social_runtime.persistence.schema import connect_database


def test_rollout_gate_accepts_only_bounded_anonymous_aggregates():
    """Catches accidental raw group or message labels in release evidence."""
    window = {
        "observation_hours": 168,
        "opportunities": 100,
        "ambient_actions": 10,
        "ambient_searches": 2,
        "p95_latency_ms": 80,
        "silence_reasons": {"low_value": 30},
        "unsupported_claims": 0,
        "stale_scene_sends": 0,
        "cross_group_leaks": 0,
        "nonknowledge_ambient_searches": 0,
        "provider_quota_anomalies": 0,
        "group_id": "must-not-be-recorded",
    }

    with pytest.raises(ValueError, match="fields"):
        importlib.import_module("eval.knowledge").evaluate_canary_rollout(
            window,
            {**window, "observation_hours": 24},
        )


def test_runtime_metrics_are_bounded_aggregates_without_private_labels(tmp_path):
    """Catches missing guardrail counters or accidental high-cardinality labels."""
    repository = KnowledgeRepository(tmp_path / "knowledge-capacity.db")
    now = 200_000
    reservation = repository.reserve_provider_quota(
        intent_hash="a" * 64,
        now=now - 20,
        hourly_limit=1,
        daily_limit=1,
    )
    assert reservation is not None
    assert repository.reserve_provider_quota(
        intent_hash="b" * 64,
        now=now - 10,
        hourly_limit=1,
        daily_limit=1,
    ) is None
    repository.finish_provider_quota(
        reservation,
        source_domains=("official.example.com",),
        latency_ms=80,
        result_kind="provider_complete",
        diagnostic_code=None,
    )
    repository.record_enrichment_usage(
        intent_hash="c" * 64,
        source_domains=("official.example.com",),
        latency_ms=4,
        cache_hit=True,
        result_kind="enrichment_cache_hit",
        diagnostic_code=None,
        now=now - 5,
    )
    for metric_kind, latency_ms in (
        ("local_resolution", 3),
        ("local_resolution", 7),
        ("scene_invalidated", 0),
        ("grounding_rejected", 0),
        ("ambient_silence", 0),
    ):
        repository.record_runtime_metric(
            metric_kind=metric_kind,
            latency_ms=latency_ms,
            diagnostic_code=None,
            now=now - 1,
        )

    with connect_database(repository.path) as db:
        db.execute(
            "INSERT INTO knowledge_entities(entity_id,entity_type,canonical_name,"
            "canonical_game_id,status,created_at,updated_at) VALUES("
            "'game:metric','game','指标测试','game:metric','active',1,1)"
        )
        db.executemany(
            "INSERT INTO knowledge_jobs(job_id,idempotency_key,job_kind,group_id,"
            "entity_id,request_json,status,attempt,next_attempt_at,diagnostic_code,"
            "created_at,updated_at) VALUES(?,?,?,?,?,'{}',?,?,?,NULL,?,?)",
            (
                ("job:pending", "key:pending", "official_daily_probe", None, "game:metric", "pending", 0, now - 30, 1, 1),
                ("job:retry", "key:retry", "official_daily_probe", None, "game:metric", "retry", 1, now + 10, 1, 1),
                ("job:running", "key:running", "official_daily_probe", None, "game:metric", "running", 1, now, 1, 1),
            ),
        )
        db.execute(
            "INSERT INTO game_release_states(version_slot_id,game_entity_id,"
            "official_label,region,platform,release_state,official_state,rumor_state,"
            "fresh_until,status,revision) VALUES('slot:metric','game:metric',NULL,"
            "'global','all','future','none','none_observed',?,'active',1)",
            (now - 20,),
        )

    metrics = repository.runtime_metrics(now=now)

    assert metrics == {
        "window_seconds": 86_400,
        "local_resolution": {"count": 2, "p95_ms": 7},
        "provider": {"calls": 1, "cache_hits": 1, "quota_rejects": 1},
        "queue": {"depth": 2, "running": 1, "job_lag_seconds": 30},
        "freshness": {"stale_slots": 1, "max_lag_seconds": 20},
        "safety": {
            "scene_invalidations": 1,
            "grounding_rejects": 1,
            "ambient_silences": 1,
        },
    }
    assert "group" not in repr(metrics).casefold()
    assert "member" not in repr(metrics).casefold()
    assert "official.example.com" not in repr(metrics)


def test_fifty_groups_keep_nonknowledge_resolution_local_and_under_budget(tmp_path):
    """Catches provider coupling or local resolver latency above the 50 ms budget."""
    repository = KnowledgeRepository(tmp_path / "resolver-capacity.db")
    SeedImporter(repository, clock=lambda: 10).import_all(load_bundled_seeds())
    resolver = KnowledgeEntityResolver(repository)

    for index in range(50):
        event = SocialEventEnvelope.create(
            event_id=f"event:{index}",
            event_type="platform.message",
            occurred_at=100,
            received_at=100,
            persona_id="groupmate:default",
            group_id=f"group:{index}",
            actor_id=f"member:{index}",
            source_message_id=f"message:{index}",
            correlation_id=f"event:{index}",
            causation_id=None,
            payload={"text": "今晚吃什么？", "direct_address": False},
        )
        frame = resolver.resolve(event, (), f"group:{index}", now=100)
        assert frame.game_ids == ()

    metrics = repository.runtime_metrics(now=100)

    assert metrics["local_resolution"]["count"] == 50
    assert metrics["local_resolution"]["p95_ms"] < 50
    assert metrics["provider"] == {
        "calls": 0,
        "cache_hits": 0,
        "quota_rejects": 0,
    }
