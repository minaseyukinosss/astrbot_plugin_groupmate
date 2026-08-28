from __future__ import annotations

import importlib

import pytest

from groupmate.social_runtime.knowledge.contracts import (
    KnowledgeClaimCandidate,
    NegativeSearchSnapshot,
    SourceEvidence,
    VersionSlot,
)
from groupmate.social_runtime.persistence.schema import connect_database


def _module():
    return importlib.import_module(
        "groupmate.social_runtime.knowledge.repository"
    )


def _repository(path):
    repository = _module().KnowledgeRepository(path)
    repository.upsert_entity(
        entity_id="game:wuthering-waves",
        entity_type="game",
        canonical_name="鸣潮",
        canonical_game_id="game:wuthering-waves",
        status="active",
        now=1,
    )
    return repository


def _slot(
    *,
    revision: int,
    official_state: str = "none",
    release_state: str = "future",
    rumor_state: str = "none_observed",
    release_checked_at: int | None = None,
    official_checked_at: int | None = None,
    rumor_checked_at: int | None = None,
):
    has_official = official_state != "none"
    return VersionSlot.create(
        version_slot_id="slot:wuthering-waves:next",
        game_entity_id="game:wuthering-waves",
        official_label="3.0" if has_official else None,
        region="cn",
        platform="all",
        release_state=release_state,
        official_state=official_state,
        rumor_state=rumor_state,
        announced_at=150 if has_official else None,
        release_at=300,
        effective_until=600,
        release_checked_at=release_checked_at,
        official_checked_at=(
            200 if has_official and official_checked_at is None else official_checked_at
        ),
        rumor_checked_at=(
            rumor_checked_at if rumor_state != "none_observed" else None
        ),
        fresh_until=86_600,
        status="active",
        revision=revision,
    )


def _source(
    *,
    source_id: str = "source:wuthering-waves:news",
    source_class: str = "official",
):
    slug = source_id.replace(":", "-")
    return SourceEvidence.create(
        evidence_id=f"evidence:{slug}",
        source_id=source_id,
        canonical_url=f"https://mc.example.com/news?id={slug}",
        domain="mc.example.com",
        publisher="Wuthering Waves",
        source_class=source_class,
        title="版本预告",
        published_at=190,
        fetched_at=200,
        evidence_excerpt="官方版本预告已公开。",
        content_hash=("c" if source_class == "official" else "d") * 64,
    )


def _negative(*, revision: int, expires_at: int = 500):
    return NegativeSearchSnapshot.create(
        snapshot_id=f"negative:{revision}:{expires_at}",
        game_entity_id="game:wuthering-waves",
        query_intent="next_version_official",
        probe_status="complete",
        covered_source_ids=("source:wuthering-waves:news",),
        required_source_ids=("source:wuthering-waves:news",),
        region="cn",
        platform="all",
        checked_at=100,
        expires_at=expires_at,
        version_state_revision=revision,
    )


def _key():
    return _module().NegativeSnapshotKey(
        game_entity_id="game:wuthering-waves",
        query_intent="next_version_official",
        region="cn",
        platform="all",
    )


def test_release_state_uses_optimistic_aggregate_revision(tmp_path):
    repository = _repository(tmp_path / "groupmate-social-runtime-v2.db")

    saved = repository.save_release_state(
        _slot(revision=1, release_checked_at=100), expected_revision=0
    )
    assert saved.revision == 1
    with pytest.raises(_module().ReleaseStateConflict):
        repository.save_release_state(
            _slot(
                revision=2,
                official_state="preview",
                release_checked_at=100,
            ),
            expected_revision=0,
        )

    updated = repository.save_release_state(
        _slot(
            revision=2,
            official_state="preview",
            release_checked_at=100,
        ),
        expected_revision=1,
    )
    assert updated.revision == 2
    assert repository.load_release_state(
        "game:wuthering-waves", "cn", "all"
    ) == (updated,)
    assert updated.release_checked_at == 100


def test_unchanged_release_state_does_not_advance_revision(tmp_path):
    repository = _repository(tmp_path / "groupmate-social-runtime-v2.db")
    repository.save_release_state(_slot(revision=1), expected_revision=0)

    saved = repository.save_release_state(
        _slot(revision=2), expected_revision=1
    )

    assert saved.revision == 1


def test_negative_snapshot_requires_current_revision_and_strict_ttl(tmp_path):
    repository = _repository(tmp_path / "groupmate-social-runtime-v2.db")
    repository.upsert_source(_source())
    repository.save_release_state(_slot(revision=1), expected_revision=0)
    repository.save_negative_snapshot(_negative(revision=1))

    current = repository.valid_negative_snapshot(_key(), now=499)
    assert current is not None
    assert current.required_source_ids == (
        "source:wuthering-waves:news",
    )
    assert repository.valid_negative_snapshot(_key(), now=500) is None

    repository.save_negative_snapshot(_negative(revision=1, expires_at=700))
    repository.save_release_state(
        _slot(revision=2, official_state="preview"), expected_revision=1
    )
    assert repository.valid_negative_snapshot(_key(), now=201) is None


def test_negative_snapshot_requires_official_required_sources(tmp_path):
    repository = _repository(tmp_path / "groupmate-social-runtime-v2.db")
    repository.upsert_source(
        _source(
            source_id="source:wuthering-waves:community",
            source_class="unofficial",
        )
    )
    repository.save_release_state(_slot(revision=1), expected_revision=0)
    snapshot = NegativeSearchSnapshot.create(
        snapshot_id="negative:unofficial",
        game_entity_id="game:wuthering-waves",
        query_intent="next_version_official",
        probe_status="complete",
        covered_source_ids=("source:wuthering-waves:community",),
        required_source_ids=("source:wuthering-waves:community",),
        region="cn",
        platform="all",
        checked_at=100,
        expires_at=500,
        version_state_revision=1,
    )

    with pytest.raises(ValueError, match="required sources must be official"):
        repository.save_negative_snapshot(snapshot)


def test_negative_snapshot_identity_is_immutable(tmp_path):
    repository = _repository(tmp_path / "groupmate-social-runtime-v2.db")
    repository.upsert_source(_source())
    repository.save_release_state(_slot(revision=1), expected_revision=0)
    repository.save_negative_snapshot(_negative(revision=1))
    conflicting = NegativeSearchSnapshot.create(
        snapshot_id="negative:1:500",
        game_entity_id="game:wuthering-waves",
        query_intent="different_intent",
        probe_status="complete",
        covered_source_ids=("source:wuthering-waves:news",),
        required_source_ids=("source:wuthering-waves:news",),
        region="cn",
        platform="all",
        checked_at=100,
        expires_at=500,
        version_state_revision=1,
    )

    with pytest.raises(ValueError, match="snapshot identity"):
        repository.save_negative_snapshot(conflicting)


def test_new_official_claim_invalidates_negative_snapshot_in_same_commit(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    repository = _repository(path)
    repository.upsert_source(_source())
    repository.save_release_state(_slot(revision=1), expected_revision=0)
    repository.save_negative_snapshot(_negative(revision=1))
    candidate = KnowledgeClaimCandidate.create(
        candidate_id="claim:preview",
        subject_entity_id="game:wuthering-waves",
        predicate="next_version_official_status",
        safe_summary="官方版本预告已公开。",
        claim_kind="public_fact",
        evidence_level="official",
        applies_to_version_slot_id="slot:wuthering-waves:next",
        region="cn",
        platform="all",
        valid_from=200,
        valid_until=86_600,
        checked_at=200,
    )

    repository.admit_claim(candidate, ("source:wuthering-waves:news",))

    assert repository.valid_negative_snapshot(_key(), now=201) is None
    with connect_database(path) as db:
        row = db.execute(
            "SELECT status,diagnostic_code FROM negative_search_snapshots"
        ).fetchone()
    assert tuple(row) == ("invalidated", "new_official_evidence")


def test_release_service_advances_each_truth_track_without_clock_release():
    """Catches illegal track jumps, cross-track overwrites, and clock releases."""
    module = importlib.import_module(
        "groupmate.social_runtime.knowledge.release_state"
    )
    service = module.GameReleaseStateService()
    state = _slot(revision=1)

    for evidence_id, track, target_state, expected_release, expected_official, expected_rumor in (
        ("evidence:release-current", "release", "current", "current", "none", "none_observed"),
        ("evidence:teaser", "official", "teaser", "current", "teaser", "none_observed"),
        ("evidence:preview", "official", "preview", "current", "preview", "none_observed"),
        ("evidence:notice", "official", "notice", "current", "notice", "none_observed"),
        ("evidence:released", "official", "released", "current", "released", "none_observed"),
        ("evidence:rumor-weak", "rumor", "weak", "current", "released", "weak"),
        ("evidence:rumor-corroborated", "rumor", "corroborated", "current", "released", "corroborated"),
        ("evidence:rumor-conflicted", "rumor", "conflicted", "current", "released", "conflicted"),
        ("evidence:rumor-stale", "rumor", "stale", "current", "released", "stale"),
        ("evidence:release-past", "release", "past", "past", "released", "stale"),
    ):
        transition = service.apply_evidence(
            state,
            module.ReleaseEvidence.create(
                evidence_id=evidence_id,
                track=track,
                target_state=target_state,
                observed_at=300,
                official_label="3.0",
            ),
        )

        assert transition.accepted is True
        assert transition.old_revision == state.revision
        assert transition.new_revision == state.revision + 1
        assert transition.evidence_ids == (evidence_id,)
        assert transition.state.release_state == expected_release
        assert transition.state.official_state == expected_official
        assert transition.state.rumor_state == expected_rumor
        state = transition.state

    invalid = service.apply_evidence(
        state,
        module.ReleaseEvidence.create(
            evidence_id="evidence:release-backward",
            track="release",
            target_state="current",
            observed_at=350,
        ),
    )
    boundary = service.apply_evidence(
        _slot(revision=1),
        module.ReleaseEvidence.create(
            evidence_id="evidence:release-boundary",
            track="release",
            target_state="future",
            observed_at=300,
        ),
    )

    assert invalid.accepted is False
    assert invalid.reason_code == "invalid_release_transition"
    assert invalid.old_revision == invalid.new_revision == state.revision
    assert boundary.revalidation_required is True
    assert boundary.state.release_state == "future"
    assert boundary.state.official_state == "none"

    current = service.apply_evidence(
        _slot(revision=1),
        module.ReleaseEvidence.create(
            evidence_id="evidence:release-current-at-boundary",
            track="release",
            target_state="current",
            observed_at=300,
        ),
    )
    stale_past = service.apply_evidence(
        current.state,
        module.ReleaseEvidence.create(
            evidence_id="evidence:release-past-stale",
            track="release",
            target_state="past",
            observed_at=200,
        ),
    )

    assert current.accepted is True
    assert current.state.release_checked_at == 300
    assert stale_past.accepted is False
    assert stale_past.reason_code == "stale_release_evidence"
    assert stale_past.state == current.state


@pytest.mark.parametrize(
    ("state", "track", "target_state", "observed_at", "reason_code"),
    [
        (_slot(revision=1), "rumor", "corroborated", 100, "invalid_rumor_transition"),
        (
            _slot(revision=1, rumor_state="weak", rumor_checked_at=100),
            "rumor",
            "conflicted",
            200,
            "invalid_rumor_transition",
        ),
        (
            _slot(revision=1, rumor_state="weak", rumor_checked_at=250),
            "rumor",
            "corroborated",
            200,
            "stale_rumor_evidence",
        ),
        (
            _slot(revision=1, official_state="teaser", official_checked_at=250),
            "official",
            "preview",
            200,
            "stale_official_evidence",
        ),
        (
            _slot(revision=1, release_checked_at=250),
            "release",
            "current",
            200,
            "stale_release_evidence",
        ),
        (_slot(revision=1), "release", "current", 299, "release_before_verified_release_at"),
        (
            _slot(revision=1, release_state="current", official_state="notice"),
            "official",
            "released",
            299,
            "official_release_before_verified_release_at",
        ),
    ],
)
def test_release_service_rejects_skipped_stale_and_early_evidence(
    state, track, target_state, observed_at, reason_code
):
    """Catches skip, stale timestamp, and pre-release transition acceptance."""
    module = importlib.import_module(
        "groupmate.social_runtime.knowledge.release_state"
    )

    transition = module.GameReleaseStateService().apply_evidence(
        state,
        module.ReleaseEvidence.create(
            evidence_id=f"evidence:{reason_code}",
            track=track,
            target_state=target_state,
            observed_at=observed_at,
        ),
    )

    assert transition.accepted is False
    assert transition.reason_code == reason_code
    assert transition.old_revision == transition.new_revision == state.revision
    assert transition.state == state
