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
    )


def test_card_uses_confirmed_pink_layout_and_repeats_requester_highlight():
    page = AffectionCardPresenter().pages(_board())[0]

    assert page.context["requester"]["rank"] == 2
    assert sum(
        item["is_requester"]
        for column in page.context["columns"]
        for item in column
    ) == 1
    assert page.context["active_count"] == 3
    assert "#fff7fa" in AFFECTION_CARD_TEMPLATE
    assert "我的位置" in AFFECTION_CARD_TEMPLATE
    assert "昵称（QQ末四位）" in AFFECTION_CARD_TEMPLATE
    assert "好感度" in AFFECTION_CARD_TEMPLATE
    assert "阶段" in AFFECTION_CARD_TEMPLATE
    assert "|e" in AFFECTION_CARD_TEMPLATE
    assert "avatar" not in AFFECTION_CARD_TEMPLATE.lower()


def test_small_board_uses_compact_content_sized_canvas():
    page = AffectionCardPresenter().pages(_board(count=1, requester_rank=1))[0]

    assert page.context["column_count"] == 1
    assert page.context["render_width"] == 920
    assert 190 <= page.context["render_height"] <= 260


def test_reference_sized_board_uses_five_dense_columns():
    page = AffectionCardPresenter().pages(_board(count=228, requester_rank=95))[0]

    assert page.context["column_count"] == 5
    assert len(page.context["columns"]) == 5
    assert max(len(column) for column in page.context["columns"]) <= 46
    assert page.context["render_width"] == 1340
    assert page.context["render_height"] <= 1180


def test_large_board_splits_only_above_240_and_sends_requester_page_first():
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
