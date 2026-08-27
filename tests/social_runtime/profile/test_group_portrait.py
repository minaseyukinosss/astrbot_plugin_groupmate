from __future__ import annotations

from groupmate.social_runtime.profile.contracts import ProfileSnapshot, SocialEdge
from groupmate.social_runtime.profile.group_portrait import GroupPortraitBuilder
from groupmate.social_runtime.profile.repository import ProfileRepository


def _snapshot(subject_id: str, role: str, private_fact: str) -> ProfileSnapshot:
    return ProfileSnapshot(
        persona_id="persona",
        group_id="group-1",
        subject_id=subject_id,
        one_line_portrait=private_fact,
        group_roles=(role,),
        individual_fingerprints=(private_fact,),
        preferences_and_boundaries=(),
        representative_episode_ids=(),
        relationship_summary="",
        maturity="forming",
        source_revision=1,
        generated_at=200,
    )


def _edge() -> SocialEdge:
    return SocialEdge(
        edge_id="edge-1",
        persona_id="persona",
        group_id="group-1",
        source_member_id="u1",
        target_member_id="u2",
        relation_type="technical_peer",
        direction="bidirectional",
        strength=0.7,
        confidence=0.91,
        source_event_ids=("event-1", "event-2", "event-3"),
        status="confirmed",
        valid_from=100,
        valid_until=None,
        last_observed_at=180,
    )


def test_group_portrait_aggregates_roles_without_leaking_private_facts():
    result = GroupPortraitBuilder().build(
        persona_id="persona",
        group_id="group-1",
        member_snapshots=(
            _snapshot("u1", "技术解答者", "不公开的旧争执"),
            _snapshot("u2", "技术解答者", "很在意某位群友"),
        ),
        culture=("重视问题落地",),
        topic_counts={"插件开发": 8, "闲聊": 2},
        activity_hours=(22, 23, 23, 0),
        edges=(_edge(),),
        source_revision=3,
        generated_at=200,
    )

    assert result.role_counts == {"技术解答者": 2}
    assert result.common_topics == ("插件开发", "闲聊")
    assert "重视问题落地" in result.summary
    assert "不公开的旧争执" not in result.summary
    assert "某位群友" not in result.summary
    assert result.relation_counts == {"technical_peer": 1}
    assert result.activity_rhythm == "深夜活跃"


def test_group_portrait_rejects_cross_scope_member_snapshots():
    cross_group = ProfileSnapshot(
        **{**_snapshot("u1", "技术解答者", "私密内容").__dict__, "group_id": "group-2"}
    )

    result = GroupPortraitBuilder().build(
        persona_id="persona",
        group_id="group-1",
        member_snapshots=(cross_group,),
        culture=(),
        topic_counts={},
        activity_hours=(),
        edges=(),
        source_revision=1,
        generated_at=200,
    )

    assert result.role_counts == {}
    assert result.member_count == 0


def test_group_portrait_has_readable_progress_summary_for_known_members():
    result = GroupPortraitBuilder().build(
        persona_id="persona",
        group_id="group-1",
        member_snapshots=(_snapshot("u1", "技术解答者", "具体但私有"),),
        culture=(),
        topic_counts={},
        activity_hours=(9,),
        edges=(),
        source_revision=1,
        generated_at=200,
    )

    assert result.summary == "已形成1位成员画像，群体认知持续更新"
    assert "具体但私有" not in result.summary


def test_group_portrait_round_trips_in_its_own_group_scope(tmp_path):
    portrait = GroupPortraitBuilder().build(
        persona_id="persona",
        group_id="group-1",
        member_snapshots=(_snapshot("u1", "技术解答者", "具体但私有"),),
        culture=("重视问题落地",),
        topic_counts={"插件开发": 3},
        activity_hours=(22,),
        edges=(),
        source_revision=2,
        generated_at=200,
    )
    repository = ProfileRepository(tmp_path / "groupmate-social-runtime-v2.db")

    repository.put_group_portrait(portrait)

    assert repository.group_portrait("persona", "group-1") == portrait
    assert repository.group_portrait("persona", "group-2") is None
