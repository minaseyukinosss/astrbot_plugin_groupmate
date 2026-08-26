from __future__ import annotations

from groupmate.social_runtime.profile.contracts import (
    MemberIdentity,
    ProfileEpisode,
    ProfileFact,
)
from groupmate.social_runtime.profile.snapshot import SnapshotBuilder


def _member() -> MemberIdentity:
    return MemberIdentity(
        persona_id="persona",
        platform="qq",
        actor_id="member-1",
        display_name="复读斥候",
        updated_at=200,
    )


def _fact(
    *,
    fact_id: str,
    category: str,
    summary: str,
    evidence_count: int,
    confidence: float = 0.94,
) -> ProfileFact:
    return ProfileFact(
        fact_id=fact_id,
        persona_id="persona",
        group_id="group-1",
        subject_id="member-1",
        category=category,
        summary=summary,
        source_kind="observed_pattern",
        source_actor_id="member-1",
        source_event_ids=tuple(
            f"event-{fact_id}-{index}" for index in range(evidence_count)
        ),
        confidence=confidence,
        status="confirmed",
        evidence_count=evidence_count,
        valid_from=100,
        injectable=True,
    )


def _episode() -> ProfileEpisode:
    return ProfileEpisode(
        episode_id="episode-layout",
        persona_id="persona",
        group_id="group-1",
        title="修复线上图片错行",
        summary="连续指出好感度卡片的字体、对齐和线上错行问题，直到修复落地。",
        participants=("member-1", "bot"),
        source_event_ids=("event-a", "event-b"),
        episode_type="shared_achievement",
        valence=0.7,
        importance=0.9,
        confidence=0.95,
        status="confirmed",
        occurred_at=120,
        last_reinforced_at=180,
    )


def test_snapshot_refuses_generic_portrait_without_discriminative_evidence():
    result = SnapshotBuilder().build(
        _member(),
        group_id="group-1",
        facts=(
            _fact(
                fact_id="generic",
                category="behavior_pattern",
                summary="友善活跃",
                evidence_count=1,
            ),
        ),
        episodes=(),
        edges=(),
        source_revision=1,
        generated_at=200,
    )

    assert result.maturity == "new"
    assert result.one_line_portrait == "正在形成画像"
    assert result.individual_fingerprints == ()


def test_snapshot_keeps_specific_behavior_boundary_and_episode():
    result = SnapshotBuilder().build(
        _member(),
        group_id="group-1",
        facts=(
            _fact(
                fact_id="behavior",
                category="behavior_pattern",
                summary="会持续追问到问题真正落地",
                evidence_count=4,
            ),
            _fact(
                fact_id="boundary",
                category="boundary",
                summary="不接受只有技术完成但用户看不懂的结果",
                evidence_count=2,
            ),
        ),
        episodes=(_episode(),),
        edges=(),
        source_revision=7,
        generated_at=200,
    )

    assert "持续追问" in result.one_line_portrait
    assert result.individual_fingerprints == ("会持续追问到问题真正落地",)
    assert result.preferences_and_boundaries == (
        "不接受只有技术完成但用户看不懂的结果",
    )
    assert result.representative_episode_ids == ("episode-layout",)
    assert result.maturity == "forming"


def test_snapshot_uses_only_confirmed_injectable_current_facts():
    proposed = _fact(
        fact_id="hearsay",
        category="preference",
        summary="据说喜欢凌晨唱歌",
        evidence_count=3,
    )
    proposed = ProfileFact(
        **{**proposed.__dict__, "status": "proposed", "injectable": False}
    )

    result = SnapshotBuilder().build(
        _member(),
        group_id="group-1",
        facts=(proposed,),
        episodes=(),
        edges=(),
        source_revision=1,
        generated_at=200,
    )

    assert "凌晨唱歌" not in result.one_line_portrait
    assert result.preferences_and_boundaries == ()
