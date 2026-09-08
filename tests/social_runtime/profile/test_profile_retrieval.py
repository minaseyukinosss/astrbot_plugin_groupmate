from __future__ import annotations

from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.profile.contracts import (
    MemberAlias,
    ProfileEpisode,
    ProfileFact,
    ProfileSnapshot,
    SocialEdge,
)
from groupmate.social_runtime.profile.repository import ProfileRepository
from groupmate.social_runtime.profile.retrieval import ProfileRetriever


def _message(*, actor="u1", mentions=(), reply_to_actor_id=None):
    return SocialEventEnvelope.create(
        event_id="event-current",
        event_type="platform.message",
        occurred_at=200,
        received_at=200,
        persona_id="persona",
        group_id="group-1",
        actor_id=actor,
        source_message_id="message-current",
        correlation_id="correlation-current",
        causation_id=None,
        payload={
            "platform": "qq",
            "text": "你们觉得这个插件怎么样",
            "mentions": list(mentions),
            "reply_to_actor_id": reply_to_actor_id,
        },
    )


def _fact(subject: str, summary: str, *, category="preference", index=1):
    return ProfileFact(
        fact_id=f"fact-{subject}-{index}",
        persona_id="persona",
        group_id="group-1",
        subject_id=subject,
        category=category,
        summary=summary,
        source_kind="self_statement",
        source_actor_id=subject,
        source_event_ids=(f"event-{subject}-{index}",),
        confidence=0.94,
        status="confirmed",
        evidence_count=2,
        valid_from=100 + index,
        injectable=True,
    )


def _snapshot(subject: str, portrait: str):
    return ProfileSnapshot(
        persona_id="persona",
        group_id="group-1",
        subject_id=subject,
        one_line_portrait=portrait,
        group_roles=(),
        individual_fingerprints=(portrait,),
        preferences_and_boundaries=(),
        representative_episode_ids=(),
        relationship_summary="",
        maturity="forming",
        source_revision=1,
        generated_at=150,
    )


def _remember_alias(repo, subject: str, alias: str):
    repo.remember_alias(
        MemberAlias(
            persona_id="persona",
            group_id="group-1",
            actor_id=subject,
            alias=alias,
            alias_type="platform_name",
            confidence=1.0,
            first_seen_at=100,
            last_seen_at=150,
        )
    )


def test_retrieval_returns_current_actor_and_at_most_two_referenced_members(tmp_path):
    repo = ProfileRepository(tmp_path / "groupmate-social-runtime-v2.db")
    for subject in ("u1", "u2", "u3", "u4"):
        _remember_alias(repo, subject, subject.upper())
        repo.put_snapshot(_snapshot(subject, f"{subject} 的具体画像"))
        repo.put_fact(_fact(subject, f"{subject} 喜欢把问题说清楚"))

    result = ProfileRetriever(repo).for_message(
        _message(actor="u1", mentions=("u2", "u3", "u4")),
        max_chars=1200,
    )

    assert [item.subject_id for item in result.members] == ["u1", "u2", "u3"]
    assert "u4" not in result.prompt_text


def test_unrelated_private_fact_is_not_injected(tmp_path):
    repo = ProfileRepository(tmp_path / "groupmate-social-runtime-v2.db")
    repo.put_fact(_fact("u1", "喜欢冷饮"))
    repo.put_fact(_fact("u2", "不公开的旧争执"))

    result = ProfileRetriever(repo).for_message(_message(actor="u1"), max_chars=1200)

    assert "喜欢冷饮" in result.prompt_text
    assert "旧争执" not in result.prompt_text


def test_retrieval_includes_bounded_episode_and_relation_for_explicit_members(tmp_path):
    repo = ProfileRepository(tmp_path / "groupmate-social-runtime-v2.db")
    repo.put_episode(
        ProfileEpisode(
            episode_id="episode-1",
            persona_id="persona",
            group_id="group-1",
            title="一起修复图片错行",
            summary="u1 和 u2 连续排查并修好了线上图片错行",
            participants=("u1", "u2"),
            source_event_ids=("e1", "e2"),
            episode_type="shared_achievement",
            valence=0.8,
            importance=0.9,
            confidence=0.95,
            status="confirmed",
            occurred_at=100,
            last_reinforced_at=150,
        )
    )
    repo.put_edge(
        SocialEdge(
            edge_id="edge-1",
            persona_id="persona",
            group_id="group-1",
            source_member_id="u1",
            target_member_id="u2",
            relation_type="technical_peer",
            direction="bidirectional",
            strength=0.7,
            confidence=0.92,
            source_event_ids=("e1", "e2", "e3"),
            status="confirmed",
            valid_from=100,
            valid_until=None,
            last_observed_at=150,
        )
    )

    result = ProfileRetriever(repo).for_message(
        _message(actor="u1", mentions=("u2",)), max_chars=1200
    )

    assert "修好了线上图片错行" in result.prompt_text
    assert "技术同伴" in result.prompt_text


def test_profile_context_obeys_hard_character_budget(tmp_path):
    repo = ProfileRepository(tmp_path / "groupmate-social-runtime-v2.db")
    for index in range(1, 9):
        repo.put_fact(
            _fact(
                "u1",
                f"第{index}条非常具体而且比较长的个人行为与偏好信息" * 5,
                index=index,
            )
        )

    result = ProfileRetriever(repo).for_message(_message(), max_chars=180)

    assert len(result.prompt_text) <= 180
    assert len(result.facts) <= 3


def test_group_member_refs_only_publish_confirmed_group_aliases(tmp_path):
    repo = ProfileRepository(tmp_path / "groupmate-social-runtime-v2.db")
    _remember_alias(repo, "u1", "小林")
    repo.remember_alias(
        MemberAlias(
            persona_id="persona",
            group_id="group-1",
            actor_id="u1",
            alias="未确认称呼",
            alias_type="inferred",
            confidence=0.5,
            first_seen_at=100,
            last_seen_at=150,
            status="candidate",
        )
    )
    _remember_alias(repo, "u2", "霞月")
    repo.remember_alias(
        MemberAlias(
            persona_id="persona",
            group_id="group-2",
            actor_id="outside",
            alias="别群成员",
            alias_type="platform_name",
            confidence=1.0,
            first_seen_at=100,
            last_seen_at=150,
        )
    )

    refs = ProfileRetriever(repo).group_member_refs("persona", "group-1")

    assert refs == {"u1": ("小林",), "u2": ("霞月",)}
    assert "未确认称呼" not in refs["u1"]


def test_retrieval_includes_speaker_relation_even_if_other_party_is_not_in_message(
    tmp_path,
):
    repo = ProfileRepository(tmp_path / "groupmate-social-runtime-v2.db")
    _remember_alias(repo, "u1", "甲")
    _remember_alias(repo, "u9", "乙")
    repo.put_edge(
        SocialEdge(
            edge_id="edge-quiet",
            persona_id="persona",
            group_id="group-1",
            source_member_id="u1",
            target_member_id="u9",
            relation_type="technical_peer",
            direction="bidirectional",
            strength=0.7,
            confidence=0.92,
            source_event_ids=("e1", "e2", "e3"),
            status="confirmed",
            valid_from=100,
            valid_until=None,
            last_observed_at=150,
        )
    )

    result = ProfileRetriever(repo).for_message(_message(actor="u1"), max_chars=1200)

    assert "技术同伴" in result.prompt_text
    assert result.edges[0].target_member_id == "u9"
    assert result.ambient_context["members"][0]["boundaries"] == []


def test_retrieval_exposes_confirmed_boundaries_for_participation(tmp_path):
    repo = ProfileRepository(tmp_path / "groupmate-social-runtime-v2.db")
    _remember_alias(repo, "u1", "甲")
    repo.put_fact(_fact("u1", "不拿考试成绩开玩笑", category="boundary"))
    repo.put_fact(_fact("u1", "喜欢冷饮", category="preference", index=2))

    result = ProfileRetriever(repo).for_message(_message(actor="u1"), max_chars=1200)

    assert result.ambient_context["members"][0]["boundaries"] == [
        "不拿考试成绩开玩笑"
    ]
    assert "不拿考试成绩开玩笑" in result.prompt_text

