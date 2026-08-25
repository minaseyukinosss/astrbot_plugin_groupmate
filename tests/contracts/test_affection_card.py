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
    assert "<svg" in AFFECTION_CARD_TEMPLATE
    assert ">♡<" not in AFFECTION_CARD_TEMPLATE
    assert "|e" in AFFECTION_CARD_TEMPLATE
    assert "avatar" not in AFFECTION_CARD_TEMPLATE.lower()


def test_small_board_keeps_fixed_six_column_structure_with_empty_slots():
    page = AffectionCardPresenter().pages(_board(count=2, requester_rank=1))[0]

    assert page.context["column_count"] == 6
    assert page.context["layout"] == "fixed"
    assert page.context["render_width"] == 1380
    assert page.context["render_height"] < 240
    assert [len(column) for column in page.context["columns"]] == [1, 1, 0, 0, 0, 0]
    assert "height={{ render_height }}" in AFFECTION_CARD_TEMPLATE


def test_card_uses_readable_roster_type_and_restrained_shadow():
    page = AffectionCardPresenter().pages(_board(count=2, requester_rank=1))[0]

    assert page.context["item_font_size"] == 13
    assert page.context["row_height"] == 23
    assert page.context["row_gap"] == 2
    assert "0 0 0 7px" not in AFFECTION_CARD_TEMPLATE
    assert "0 20px 46px" not in AFFECTION_CARD_TEMPLATE
    assert "0 8px 20px" not in AFFECTION_CARD_TEMPLATE
    assert "0 5px 14px" not in AFFECTION_CARD_TEMPLATE
    assert "0 3px 8px rgba(128, 24, 63, .08)" in AFFECTION_CARD_TEMPLATE


def test_card_title_uses_clean_compact_typography():
    assert "font-size: 27px" in AFFECTION_CARD_TEMPLATE
    assert "font-weight: 800" in AFFECTION_CARD_TEMPLATE
    assert "letter-spacing: 0" in AFFECTION_CARD_TEMPLATE
    assert "font-weight: 880" not in AFFECTION_CARD_TEMPLATE
    assert "letter-spacing: .03em" not in AFFECTION_CARD_TEMPLATE


@pytest.mark.parametrize(
    ("count", "layout", "columns", "pages"),
    (
        (10, "fixed", 6, 1),
        (11, "fixed", 6, 1),
        (50, "fixed", 6, 1),
        (51, "fixed", 6, 1),
        (100, "fixed", 6, 1),
        (228, "fixed", 6, 1),
        (240, "fixed", 6, 1),
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
