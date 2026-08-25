from __future__ import annotations

from groupmate.social_runtime.society.affection_leaderboard import (
    AffectionLeaderboardService,
)
from groupmate.social_runtime.society.relationships import (
    PublicAffection,
    RelationshipStage,
)


class FakeRelationships:
    def __init__(self, values):
        self.values = values
        self.scopes = []

    def relationship_snapshot(self, persona_id, group_id, subject_id):
        self.scopes.append((persona_id, group_id, subject_id))
        return None, self.values.get(
            subject_id,
            PublicAffection(0.0, RelationshipStage.STRANGER),
        )


def test_leaderboard_is_sorted_and_highlights_requester_without_full_ids():
    repository = FakeRelationships(
        {
            "10001234": PublicAffection(12.3, RelationshipStage.KNOWS),
            "20005678": PublicAffection(-4.0, RelationshipStage.STRANGER),
            "30009012": PublicAffection(35.0, RelationshipStage.FAMILIAR),
        }
    )
    service = AffectionLeaderboardService(repository)

    result = service.build(
        persona_id="aemeath",
        group_id="885617919",
        group_name="小饼干回收部",
        requester_id="10001234",
        members=(
            {"actor_id": "10001234", "display_name": "夏夏", "updated_at": 90},
            {"actor_id": "20005678", "display_name": "阿明", "updated_at": 91},
            {"actor_id": "30009012", "display_name": "小雨", "updated_at": 92},
        ),
        updated_at=100,
    )

    assert [item.display_name for item in result.entries] == ["小雨", "夏夏", "阿明"]
    assert result.requester.rank == 2
    assert result.requester.is_requester is True
    assert result.requester.platform_tail == "1234"
    assert result.requester.score == 12.3
    assert result.entries[0].stage == "熟悉"
    assert "10001234" not in str(result.public_context())
    assert repository.scopes == [
        ("aemeath", "885617919", "10001234"),
        ("aemeath", "885617919", "20005678"),
        ("aemeath", "885617919", "30009012"),
    ]


def test_text_fallback_always_contains_requester_rank_score_and_stage():
    result = AffectionLeaderboardService(FakeRelationships({})).build(
        persona_id="aemeath",
        group_id="g",
        group_name="测试群",
        requester_id="42",
        members=(
            {"actor_id": "42", "display_name": "查询者", "updated_at": 100},
        ),
        updated_at=100,
    )

    assert result.text_fallback() == (
        "查询者，你在本群好感度榜第 1/1 名：0.0（陌生）。\n"
        "榜单图片暂时生成失败，稍后再试也可以。"
    )
