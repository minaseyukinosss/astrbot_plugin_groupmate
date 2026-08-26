from __future__ import annotations

from groupmate.social_runtime.profile.contracts import (
    MemberIdentity,
    ProfileEpisode,
    ProfileFact,
    ProfileObservation,
    ProfileSnapshot,
    SocialEdge,
)
from groupmate.social_runtime.profile.repository import ProfileRepository


def _fact(*, fact_id: str, group_id: str, summary: str) -> ProfileFact:
    return ProfileFact(
        fact_id=fact_id,
        persona_id="persona",
        group_id=group_id,
        subject_id="member-1",
        category="preference",
        summary=summary,
        source_kind="self_statement",
        source_actor_id="member-1",
        source_event_ids=("event-1",),
        confidence=0.95,
        status="confirmed",
        evidence_count=1,
        valid_from=100,
        injectable=True,
    )


def test_profile_repository_never_crosses_group_scope(tmp_path):
    repo = ProfileRepository(tmp_path / "groupmate-social-runtime-v2.db")
    repo.upsert_identity(
        MemberIdentity(
            persona_id="persona",
            platform="qq",
            actor_id="member-1",
            display_name="群友甲",
            updated_at=100,
        )
    )
    repo.put_fact(
        _fact(fact_id="fact-1", group_id="group-1", summary="喜欢冷饮")
    )

    assert [
        item.summary for item in repo.facts("persona", "group-1", "member-1")
    ] == ["喜欢冷饮"]
    assert repo.facts("persona", "group-2", "member-1") == ()


def test_profile_fact_replay_is_idempotent(tmp_path):
    repo = ProfileRepository(tmp_path / "groupmate-social-runtime-v2.db")
    fact = _fact(
        fact_id="fact-stable",
        group_id="group-1",
        summary="不喜欢含糊解释",
    )

    first = repo.put_fact(fact)
    second = repo.put_fact(fact)

    assert first == second == fact
    assert len(repo.facts("persona", "group-1", "member-1")) == 1


def test_injectable_query_excludes_pending_fact(tmp_path):
    repo = ProfileRepository(tmp_path / "groupmate-social-runtime-v2.db")
    confirmed = _fact(
        fact_id="fact-confirmed",
        group_id="group-1",
        summary="会持续追问到问题落地",
    )
    pending = ProfileFact(
        **{
            **confirmed.__dict__,
            "fact_id": "fact-pending",
            "summary": "别人说他讨厌所有人",
            "source_kind": "third_party_claim",
            "source_actor_id": "member-2",
            "status": "proposed",
            "injectable": False,
        }
    )
    repo.put_fact(confirmed)
    repo.put_fact(pending)

    assert repo.facts(
        "persona", "group-1", "member-1", injectable_only=True
    ) == (confirmed,)


def test_observation_claim_and_completion_are_group_scoped(tmp_path):
    repo = ProfileRepository(tmp_path / "groupmate-social-runtime-v2.db")
    for group_id in ("group-1", "group-2"):
        repo.enqueue_observation(
            ProfileObservation(
                event_id=f"event-{group_id}",
                persona_id="persona",
                group_id=group_id,
                actor_id="member-1",
                payload={"text": "我喜欢冷饮"},
                occurred_at=100,
            )
        )

    claimed = repo.claim_observations(
        "persona", "group-1", limit=20, now=100
    )
    repo.complete_observations(
        tuple(item.event_id for item in claimed),
        status="completed",
        diagnostic_code=None,
    )

    assert [item.event_id for item in claimed] == ["event-group-1"]
    assert repo.claim_observations(
        "persona", "group-1", limit=20, now=100
    ) == ()
    assert [
        item.event_id
        for item in repo.claim_observations(
            "persona", "group-2", limit=20, now=100
        )
    ] == ["event-group-2"]


def test_profile_episode_round_trips_for_one_member(tmp_path):
    repo = ProfileRepository(tmp_path / "groupmate-social-runtime-v2.db")
    episode = ProfileEpisode(
        episode_id="episode-1",
        persona_id="persona",
        group_id="group-1",
        title="一起修复图片错行",
        summary="成员持续反馈字体错行，最终完成修复。",
        participants=("member-1", "bot"),
        source_event_ids=("event-1", "event-2"),
        episode_type="shared_achievement",
        valence=0.8,
        importance=0.9,
        confidence=0.95,
        status="confirmed",
        occurred_at=100,
        last_reinforced_at=120,
    )

    repo.put_episode(episode)

    assert repo.episodes("persona", "group-1", "member-1") == (episode,)
    assert repo.episodes("persona", "group-2", "member-1") == ()


def test_social_edge_round_trips_without_nickname_resolution(tmp_path):
    repo = ProfileRepository(tmp_path / "groupmate-social-runtime-v2.db")
    edge = SocialEdge(
        edge_id="edge-1",
        persona_id="persona",
        group_id="group-1",
        source_member_id="member-1",
        target_member_id="member-2",
        relation_type="technical_peer",
        direction="bidirectional",
        strength=0.7,
        confidence=0.9,
        source_event_ids=("event-1",),
        status="confirmed",
        valid_from=100,
        valid_until=None,
        last_observed_at=120,
    )

    repo.put_edge(edge)

    assert repo.edges("persona", "group-1", "member-1") == (edge,)
    assert repo.edges("persona", "group-2", "member-1") == ()


def test_latest_profile_snapshot_replaces_older_revision(tmp_path):
    repo = ProfileRepository(tmp_path / "groupmate-social-runtime-v2.db")
    first = ProfileSnapshot(
        persona_id="persona",
        group_id="group-1",
        subject_id="member-1",
        one_line_portrait="正在形成画像",
        group_roles=(),
        individual_fingerprints=(),
        preferences_and_boundaries=(),
        representative_episode_ids=(),
        relationship_summary="",
        maturity="new",
        source_revision=1,
        generated_at=100,
    )
    current = ProfileSnapshot(
        **{
            **first.__dict__,
            "one_line_portrait": "会持续追问到问题真正落地",
            "individual_fingerprints": ("重视实际体验",),
            "maturity": "forming",
            "source_revision": 2,
            "generated_at": 120,
        }
    )

    repo.put_snapshot(first)
    repo.put_snapshot(current)

    assert repo.snapshot("persona", "group-1", "member-1") == current
    assert repo.snapshot("persona", "group-2", "member-1") is None
