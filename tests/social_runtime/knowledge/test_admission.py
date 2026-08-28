from __future__ import annotations

import importlib

import pytest

from groupmate.social_runtime.knowledge.contracts import (
    KnowledgeClaimCandidate,
    SourceEvidence,
)
from groupmate.social_runtime.persistence.schema import connect_database


def _repository(path):
    module = importlib.import_module(
        "groupmate.social_runtime.knowledge.repository"
    )
    repository = module.KnowledgeRepository(path)
    repository.upsert_entity(
        entity_id="game:genshin-impact",
        entity_type="game",
        canonical_name="原神",
        canonical_game_id="game:genshin-impact",
        status="active",
        now=1,
    )
    return repository


def _source(
    source_id: str,
    *,
    source_class: str = "official",
    fetched_at: int = 100,
):
    slug = source_id.replace(":", "-")
    return SourceEvidence.create(
        evidence_id=f"evidence:{slug}",
        source_id=source_id,
        canonical_url=f"https://game.example.com/news?id={slug}",
        domain="game.example.com",
        publisher="Example Game",
        source_class=source_class,
        title="版本消息",
        published_at=fetched_at - 1,
        fetched_at=fetched_at,
        evidence_excerpt="一条有界的版本证据。",
        content_hash=("a" if source_id.endswith("1") else "b") * 64,
    )


def _candidate(
    candidate_id: str,
    *,
    summary: str,
    checked_at: int,
    evidence_level: str = "official",
    claim_kind: str = "public_fact",
):
    return KnowledgeClaimCandidate.create(
        candidate_id=candidate_id,
        subject_entity_id="game:genshin-impact",
        predicate="next_version_official_status",
        safe_summary=summary,
        claim_kind=claim_kind,
        evidence_level=evidence_level,
        applies_to_version_slot_id="slot:next",
        region="cn",
        platform="all",
        valid_from=checked_at,
        valid_until=checked_at + 86_400,
        checked_at=checked_at,
    )


def test_claim_and_all_evidence_links_commit_atomically(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    repository = _repository(path)
    repository.upsert_source(_source("source:official:1"))

    with pytest.raises(ValueError, match="source evidence does not exist"):
        repository.admit_claim(
            _candidate("claim:1", summary="已有预告。", checked_at=100),
            ("source:official:1", "source:missing"),
        )

    with connect_database(path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM knowledge_claims WHERE claim_id='claim:1'"
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM knowledge_claim_evidence"
        ).fetchone()[0] == 0


def test_claim_cannot_self_assert_official_level_from_unofficial_sources(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    repository = _repository(path)
    repository.upsert_source(
        _source("source:rumor:1", source_class="unofficial")
    )

    with pytest.raises(ValueError, match="does not support evidence level"):
        repository.admit_claim(
            _candidate("claim:forged", summary="自称官方。", checked_at=100),
            ("source:rumor:1",),
        )

    with connect_database(path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM knowledge_claims"
        ).fetchone()[0] == 0


def test_newer_official_claim_supersedes_old_claim_without_moving_evidence(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    repository = _repository(path)
    repository.upsert_source(_source("source:official:1"))
    repository.upsert_source(_source("source:official:2", fetched_at=200))

    first = repository.admit_claim(
        _candidate("claim:old", summary="尚未公开。", checked_at=100),
        ("source:official:1",),
    )
    second = repository.admit_claim(
        _candidate("claim:new", summary="已经公开预告。", checked_at=200),
        ("source:official:2",),
    )

    assert first.outcome == "inserted"
    assert second.outcome == "superseded"
    assert second.superseded_claim_ids == ("claim:old",)
    with connect_database(path) as db:
        claims = db.execute(
            "SELECT claim_id,status,supersedes_claim_id FROM knowledge_claims "
            "ORDER BY claim_id"
        ).fetchall()
        links = db.execute(
            "SELECT claim_id,source_id FROM knowledge_claim_evidence "
            "ORDER BY claim_id"
        ).fetchall()
    assert [tuple(row) for row in claims] == [
        ("claim:new", "active", "claim:old"),
        ("claim:old", "superseded", None),
    ]
    assert [tuple(row) for row in links] == [
        ("claim:new", "source:official:2"),
        ("claim:old", "source:official:1"),
    ]


def test_equal_checked_at_does_not_supersede_active_claim(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    repository = _repository(path)
    repository.upsert_source(_source("source:official:1"))
    repository.upsert_source(_source("source:official:2"))
    repository.admit_claim(
        _candidate("claim:first", summary="第一条。", checked_at=0),
        ("source:official:1",),
    )

    result = repository.admit_claim(
        _candidate("claim:equal", summary="同一时刻。", checked_at=0),
        ("source:official:2",),
    )

    assert result.outcome == "unchanged"
    assert result.claim_id == "claim:first"
    with connect_database(path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM knowledge_claims"
        ).fetchone()[0] == 1


def test_official_claim_preserves_rumor_history_and_classification(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    repository = _repository(path)
    repository.upsert_source(
        _source("source:rumor:1", source_class="unofficial")
    )
    repository.upsert_source(_source("source:official:2", fetched_at=200))
    repository.admit_claim(
        _candidate(
            "claim:rumor",
            summary="传闻下版本有某角色。",
            checked_at=100,
            evidence_level="unofficial",
            claim_kind="rumor",
        ),
        ("source:rumor:1",),
    )

    repository.admit_claim(
        _candidate("claim:official", summary="官方公布版本主题。", checked_at=200),
        ("source:official:2",),
    )

    with connect_database(path) as db:
        rumor = db.execute(
            "SELECT claim_kind,evidence_level,status FROM knowledge_claims "
            "WHERE claim_id='claim:rumor'"
        ).fetchone()
        source = db.execute(
            "SELECT source_class FROM knowledge_sources "
            "WHERE source_id='source:rumor:1'"
        ).fetchone()
    assert tuple(rumor) == ("rumor", "unofficial", "active")
    assert source[0] == "unofficial"


@pytest.mark.parametrize(
    ("candidate", "existing", "outcome", "reason_code", "superseded_ids"),
    [
        (
            _candidate(
                "claim:bundled-semantic",
                summary="提瓦特是原神世界。",
                checked_at=100,
                evidence_level="bundled",
                claim_kind="stable_semantic",
            ),
            (),
            "activate",
            "bundled_stable_semantic",
            (),
        ),
        (
            _candidate(
                "claim:bundled-fact",
                summary="版本已经发布。",
                checked_at=100,
                evidence_level="bundled",
            ),
            (),
            "reject",
            "bundled_requires_stable_semantic",
            (),
        ),
        (
            _candidate(
                "claim:official",
                summary="官方预告已公开。",
                checked_at=100,
            ),
            (),
            "activate",
            "official_public_fact",
            (),
        ),
        (
            _candidate(
                "claim:secondary",
                summary="可靠媒体报道版本消息。",
                checked_at=100,
                evidence_level="secondary",
                claim_kind="stable_semantic",
            ),
            (),
            "keep_pending",
            "secondary_requires_corroboration",
            (),
        ),
        (
            _candidate(
                "claim:corroborated",
                summary="可靠媒体一致报道版本消息。",
                checked_at=100,
                evidence_level="corroborated",
                claim_kind="stable_semantic",
            ),
            (),
            "activate",
            "corroborated_stable_semantic",
            (),
        ),
        (
            _candidate(
                "claim:unofficial",
                summary="传闻下版本有角色。",
                checked_at=100,
                evidence_level="unofficial",
                claim_kind="rumor",
            ),
            (),
            "activate",
            "unofficial_rumor",
            (),
        ),
        (
            _candidate(
                "claim:conflict",
                summary="没有预告。",
                checked_at=100,
            ),
            (
                _candidate(
                    "claim:existing",
                    summary="官方预告已公开。",
                    checked_at=100,
                ),
            ),
            "dispute",
            "equal_evidence_conflict",
            (),
        ),
        (
            _candidate(
                "claim:newer",
                summary="官方预告已公开。",
                checked_at=200,
            ),
            (
                _candidate(
                    "claim:older",
                    summary="尚未公开预告。",
                    checked_at=100,
                ),
            ),
            "supersede",
            "newer_official_supersedes",
            ("claim:older",),
        ),
        (
            _candidate(
                "claim:release",
                summary="正式版本已实装。",
                checked_at=200,
                claim_kind="public_fact",
            ),
            (
                _candidate(
                    "claim:test-server",
                    summary="测试服数值变动。",
                    checked_at=100,
                    evidence_level="unofficial",
                    claim_kind="rumor",
                ),
            ),
            "activate",
            "official_release_outranks_nonofficial",
            (),
        ),
    ],
)
def test_admission_policy_enforces_evidence_ladder_and_conflict_history(
    candidate, existing, outcome, reason_code, superseded_ids
):
    """Catches unsafe activation, incorrect conflict ranking, or rumor rewrites."""
    module = importlib.import_module(
        "groupmate.social_runtime.knowledge.admission"
    )

    decision = module.KnowledgeAdmissionPolicy().evaluate(candidate, existing)

    assert decision.outcome == outcome
    assert decision.reason_code == reason_code
    assert decision.superseded_claim_ids == superseded_ids
