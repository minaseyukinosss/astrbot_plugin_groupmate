from __future__ import annotations

from groupmate.social_runtime.profile.contracts import (
    ProfileFactCandidate,
    SocialEdgeCandidate,
)
from groupmate.social_runtime.profile.policy import ProfileEvidencePolicy
from groupmate.social_runtime.profile.repository import ProfileRepository


def _candidate(**overrides) -> ProfileFactCandidate:
    values = {
        "candidate_id": "candidate-1",
        "persona_id": "persona",
        "group_id": "group-1",
        "subject_id": "member-1",
        "category": "preference",
        "summary": "喜欢冷饮",
        "source_kind": "self_statement",
        "source_actor_id": "member-1",
        "source_event_ids": ("event-1",),
        "confidence": 0.91,
        "evidence_count": 1,
        "observed_at": 100,
    }
    values.update(overrides)
    return ProfileFactCandidate(**values)


def _edge_candidate(**overrides) -> SocialEdgeCandidate:
    values = {
        "candidate_id": "edge-candidate-1",
        "persona_id": "persona",
        "group_id": "group-1",
        "source_member_id": "member-1",
        "target_member_id": "member-2",
        "relation_type": "technical_peer",
        "direction": "bidirectional",
        "strength": 0.7,
        "confidence": 0.91,
        "source_event_ids": ("event-1", "event-2", "event-3"),
        "observed_at": 100,
    }
    values.update(overrides)
    return SocialEdgeCandidate(**values)


def test_self_statement_can_confirm_but_third_party_claim_stays_pending():
    policy = ProfileEvidencePolicy()

    own = policy.decide(_candidate(), allowed_event_ids={"event-1"})
    hearsay = policy.decide(
        _candidate(
            candidate_id="candidate-2",
            source_kind="third_party_claim",
            source_actor_id="member-2",
            confidence=0.99,
        ),
        allowed_event_ids={"event-1"},
    )

    assert (own.status, own.injectable) == ("confirmed", True)
    assert (hearsay.status, hearsay.injectable) == ("proposed", False)


def test_repeated_observation_requires_three_independent_evidence_items():
    policy = ProfileEvidencePolicy()

    weak = policy.decide(
        _candidate(
            source_kind="observed_pattern",
            source_event_ids=("event-1", "event-2"),
            evidence_count=2,
            confidence=0.95,
        ),
        allowed_event_ids={"event-1", "event-2", "event-3"},
    )
    stable = policy.decide(
        _candidate(
            candidate_id="candidate-stable",
            source_kind="observed_pattern",
            source_event_ids=("event-1", "event-2", "event-3"),
            evidence_count=3,
            confidence=0.9,
        ),
        allowed_event_ids={"event-1", "event-2", "event-3"},
    )

    assert (weak.status, weak.injectable) == ("proposed", False)
    assert (stable.status, stable.injectable) == ("confirmed", True)


def test_candidate_with_unknown_evidence_is_rejected():
    fact = ProfileEvidencePolicy().decide(
        _candidate(source_event_ids=("invented-event",)),
        allowed_event_ids={"event-1"},
    )

    assert (fact.status, fact.injectable) == ("rejected", False)


def test_sensitive_relationship_candidate_is_rejected():
    edge = ProfileEvidencePolicy().decide_edge(
        _edge_candidate(relation_type="romantic"),
        allowed_event_ids={"event-1", "event-2", "event-3"},
    )

    assert edge.status == "rejected"


def test_admin_correction_supersedes_old_fact_immediately(tmp_path):
    policy = ProfileEvidencePolicy()
    repository = ProfileRepository(tmp_path / "groupmate-social-runtime-v2.db")
    old = policy.decide(_candidate(), allowed_event_ids={"event-1"})
    repository.put_fact(old)
    correction = policy.correct(
        old,
        _candidate(
            candidate_id="candidate-corrected",
            summary="现在不喝冷饮",
            source_kind="admin_correction",
            source_actor_id="admin-1",
            source_event_ids=("correction-1",),
            observed_at=200,
            confidence=1.0,
        ),
    )

    repository.replace_fact(
        correction,
        audit_id="audit-1",
        actor_id="admin-1",
        created_at=200,
    )

    injectable = repository.facts(
        "persona", "group-1", "member-1", injectable_only=True
    )
    all_facts = repository.facts("persona", "group-1", "member-1")
    assert injectable == (correction.new,)
    assert [item.status for item in all_facts] == ["superseded", "confirmed"]
    assert correction.new.supersedes_fact_id == old.fact_id


def test_stale_or_invalidated_fact_stops_injection_but_keeps_history(tmp_path):
    policy = ProfileEvidencePolicy()
    repository = ProfileRepository(tmp_path / "groupmate-social-runtime-v2.db")
    stale = policy.decide(_candidate(), allowed_event_ids={"event-1"})
    removed = policy.decide(
        _candidate(candidate_id="candidate-removed", summary="曾经喜欢熬夜"),
        allowed_event_ids={"event-1"},
    )
    repository.put_fact(stale)
    repository.put_fact(removed)

    repository.change_fact_status(
        stale.fact_id,
        persona_id="persona",
        group_id="group-1",
        subject_id="member-1",
        status="stale",
        audit_id="audit-stale",
        actor_id="system",
        created_at=300,
    )
    repository.change_fact_status(
        removed.fact_id,
        persona_id="persona",
        group_id="group-1",
        subject_id="member-1",
        status="rejected",
        audit_id="audit-delete",
        actor_id="member-1",
        created_at=301,
    )

    assert repository.facts(
        "persona", "group-1", "member-1", injectable_only=True
    ) == ()
    assert [
        item.status
        for item in repository.facts("persona", "group-1", "member-1")
    ] == ["stale", "rejected"]
