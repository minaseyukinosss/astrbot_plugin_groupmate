import pytest

from groupmate.adapters.affection_card import (
    AFFECTION_CARD_TEMPLATE,
    AffectionCardPresenter,
)
from groupmate.social_runtime.society.affection_leaderboard import (
    AffectionLeaderboard,
    AffectionLeaderboardEntry,
)


def _board(count=3, requester_rank=2):
    entries = tuple(
        AffectionLeaderboardEntry(
            rank=index,
            display_name=f"成员{index}",
            platform_tail=f"{index:04d}",
            score=round(10 - index / 10, 1),
            stage="认识",
            is_requester=index == requester_rank,
        )
        for index in range(1, count + 1)
    )
    return AffectionLeaderboard(
        group_id="885617919",
        group_name="小饼干回收部",
        updated_at=100,
        entries=entries,
        recent_active_count=min(count, 7),
    )


def test_card_keeps_one_requester_highlight_and_clear_member_counts():
    page = AffectionCardPresenter().pages(_board())[0]

    assert page.context["requester"]["rank"] == 2
    assert sum(
        item["is_requester"]
        for column in page.context["columns"]
        for item in column
    ) == 1
    assert page.context["member_count"] == 3
    assert page.context["recent_active_count"] == 3
    assert "#fffafd" in AFFECTION_CARD_TEMPLATE
    assert "我的排名" in AFFECTION_CARD_TEMPLATE
    assert "群成员" in AFFECTION_CARD_TEMPLATE
    assert "近 30 天互动" in AFFECTION_CARD_TEMPLATE
    assert "member-pill" in AFFECTION_CARD_TEMPLATE
    assert "好感度" in AFFECTION_CARD_TEMPLATE
    assert "column-head" not in AFFECTION_CARD_TEMPLATE
    assert "|e" in AFFECTION_CARD_TEMPLATE
    assert "avatar" not in AFFECTION_CARD_TEMPLATE.lower()


def test_small_board_uses_compact_content_sized_canvas():
    page = AffectionCardPresenter().pages(_board(count=1, requester_rank=1))[0]

    assert page.context["column_count"] == 1
    assert page.context["layout"] == "small"
    assert page.context["render_width"] == 820
    assert page.context["render_height"] <= 600


@pytest.mark.parametrize(
    ("count", "layout", "columns", "pages"),
    (
        (10, "small", 1, 1),
        (11, "medium", 3, 1),
        (50, "medium", 3, 1),
        (51, "large", 6, 1),
        (100, "large", 6, 1),
        (228, "large", 6, 1),
        (240, "large", 6, 1),
        (241, "paged", 6, 2),
    ),
)
def test_layout_adapts_to_group_size_without_exceeding_capture_viewport(
    count, layout, columns, pages
):
    result = AffectionCardPresenter().pages(
        _board(count=count, requester_rank=count)
    )

    assert len(result) == pages
    assert result[0].context["layout"] == layout
    assert result[0].context["column_count"] == columns
    assert result[0].context["render_width"] <= 1380
    assert all(page.context["render_height"] <= 1200 for page in result)


def test_large_board_pages_at_240_and_sends_requester_page_first():
    presenter = AffectionCardPresenter()

    assert len(presenter.pages(_board(count=240, requester_rank=1))) == 1
    pages = presenter.pages(_board(count=241, requester_rank=241))

    assert len(pages) == 2
    assert pages[0].page_number == 2
    assert any(
        item["is_requester"]
        for column in pages[0].context["columns"]
        for item in column
    )
    assert pages[1].page_number == 1
    assert {
        item["rank"]
        for page in pages
        for column in page.context["columns"]
        for item in column
    } == set(range(1, 242))


def test_incomplete_roster_is_marked_on_the_card():
    board = _board(count=3, requester_rank=1)
    incomplete = AffectionLeaderboard(
        group_id=board.group_id,
        group_name=board.group_name,
        updated_at=board.updated_at,
        entries=board.entries,
        recent_active_count=board.recent_active_count,
        roster_complete=False,
    )

    page = AffectionCardPresenter().pages(incomplete)[0]

    assert page.context["roster_complete"] is False
    assert "名单暂未完全同步" in AFFECTION_CARD_TEMPLATE
