"""Presentation-only data and template for the public affection card."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..social_runtime.society.affection_leaderboard import (
    AffectionLeaderboard,
    AffectionLeaderboardEntry,
)


AFFECTION_CARD_TEMPLATE = r"""
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width={{ render_width }}, height={{ render_height }}, initial-scale=1">
<style>
  * { box-sizing: border-box; }
  html { width: {{ render_width }}px; height: {{ render_height }}px; margin: 0; padding: 0; background: transparent; }
  body {
    width: {{ render_width }}px; height: {{ render_height }}px;
    margin: 0; padding: 20px;
    background:
      radial-gradient(circle at 8% 0%, rgba(255, 184, 211, .42), transparent 28%),
      radial-gradient(circle at 92% 8%, rgba(255, 226, 238, .86), transparent 34%),
      linear-gradient(145deg, #fffafd 0%, #fff1f6 56%, #fff8fb 100%);
    color: #432b35;
    font-family: Inter, "SF Pro Text", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  main {
    width: 100%; padding: 20px;
    border: 1px solid rgba(219, 95, 139, .40); border-radius: 22px;
    background: rgba(255, 255, 255, .76);
    box-shadow:
      0 3px 8px rgba(128, 24, 63, .08),
      inset 0 1px 0 rgba(255, 255, 255, .96);
  }
  header {
    display: grid; grid-template-columns: 58px minmax(0, 1fr) auto;
    align-items: center; gap: 16px; height: 70px; margin-bottom: 14px;
  }
  .heart {
    display: grid; place-items: center; width: 54px; height: 54px;
    border: 1px solid rgba(255, 255, 255, .82); border-radius: 16px;
    background: linear-gradient(145deg, #ff80aa, #ed3f77); color: #fff;
    box-shadow: 0 4px 8px rgba(176, 22, 79, .18), inset 0 1px 2px rgba(255, 255, 255, .42);
  }
  .heart svg { display: block; width: 31px; height: 31px; }
  .summary { min-width: 0; }
  .heading { display: flex; min-width: 0; align-items: baseline; gap: 14px; }
  h1 { flex: 0 0 auto; margin: 0; color: #761039; font-size: 27px; line-height: 1; font-weight: 800; letter-spacing: 0; }
  .group-name { min-width: 0; overflow: hidden; color: #725661; font-size: 14px; font-weight: 720; text-overflow: ellipsis; white-space: nowrap; }
  .meta, .sync-note { margin: 6px 0 0; color: #997581; font-size: 12px; line-height: 1.2; white-space: nowrap; }
  .sync-note { margin-left: 8px; color: #b46d26; font-weight: 700; }
  .mine-rank {
    min-width: 136px; padding: 9px 16px; border: 1px solid rgba(255, 255, 255, .86); border-radius: 999px;
    background: rgba(255, 255, 255, .78); color: #9c3158; text-align: center;
    box-shadow: 0 3px 8px rgba(127, 27, 64, .07), inset 0 1px 0 #fff;
  }
  .mine-rank span { display: block; font-size: 10px; font-weight: 740; letter-spacing: .08em; }
  .mine-rank strong { display: block; margin-top: 2px; color: #7f173d; font-size: 14px; font-weight: 850; font-variant-numeric: tabular-nums; }
  .columns {
    display: grid; grid-template-columns: repeat({{ column_count }}, minmax(0, 1fr));
    align-items: start; gap: {{ column_gap }}px; margin: 0; padding: 0;
  }
  .rank-column { display: flex; min-width: 0; flex-direction: column; gap: {{ row_gap }}px; }
  .member-pill {
    display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 8px;
    height: {{ row_height }}px; min-width: 0; padding: 0 11px;
    border: 1px solid rgba(255, 255, 255, .90); border-radius: 10px;
    background: rgba(255, 255, 255, .69);
    box-shadow: 0 1px 3px rgba(107, 24, 55, .04), inset 0 1px 0 rgba(255, 255, 255, .86);
  }
  .member-pill.me {
    border-color: #ff5c8d; background: linear-gradient(90deg, #ffe1eb, #fff5f8);
    box-shadow: 0 0 0 2px rgba(255, 92, 141, .12), 0 3px 9px rgba(151, 25, 72, .10);
  }
  .identity { display: flex; min-width: 0; align-items: center; gap: 4px; }
  .name { min-width: 0; overflow: hidden; color: #4a3540; font-size: {{ item_font_size }}px; font-weight: 670; text-overflow: ellipsis; white-space: nowrap; }
  .tail { flex: 0 0 auto; color: #ad979f; font-size: calc({{ item_font_size }}px - 2px); font-variant-numeric: tabular-nums; }
  .score { color: #b12355; font-size: {{ item_font_size }}px; font-weight: 850; font-variant-numeric: tabular-nums; }
  .score.negative { color: #dc433e; }
  .you { flex: 0 0 auto; padding: 1px 5px; border-radius: 999px; background: #ff5c8d; color: #fff; font-size: 9px; font-style: normal; font-weight: 800; }
  footer { height: 18px; padding: 6px 4px 0; color: #9b7c88; font-size: 10px; text-align: right; }
</style>
</head>
<body class="layout-{{ layout|e }}">
<main>
  <header>
    <span class="heart" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78L12 21.23l8.84-8.84a5.5 5.5 0 0 0 0-7.78Z" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
    <div class="summary">
      <div class="heading"><h1>好感度</h1><span class="group-name">{{ group_name|e }}</span></div>
      <p class="meta">群 {{ group_id|e }}　·　群成员 {{ member_count }} 人　·　近 30 天互动 {{ recent_active_count }} 人　·　更新于 {{ updated_text|e }}{% if not roster_complete %}<span class="sync-note">名单暂未完全同步</span>{% endif %}</p>
    </div>
    <div class="mine-rank">
      <span>我的排名</span><strong>{{ requester.rank }} / {{ member_count }}</strong>
    </div>
  </header>
  <div class="columns cols-{{ column_count }}">
  {% for column in columns %}
    <section class="rank-column">
    {% for item in column %}
      <div class="member-pill{% if item.is_requester %} me{% endif %}">
        <span class="identity"><span class="name">{{ item.display_name|e }}</span><span class="tail">{{ item.platform_tail|e }}</span>{% if item.is_requester %}<b class="you">你</b>{% endif %}</span>
        <strong class="score {{ item.score_tone|e }}">{{ item.score_text|e }}</strong>
      </div>
    {% endfor %}
    </section>
  {% endfor %}
  </div>
  {% if page_count > 1 %}<footer>第 {{ page_number }} / {{ page_count }} 页</footer>{% endif %}
</main>
</body>
</html>
"""


@dataclass(frozen=True)
class AffectionCardPage:
    page_number: int
    page_count: int
    context: dict[str, object]


class AffectionCardPresenter:
    PAGE_SIZE = 240

    def pages(
        self, leaderboard: AffectionLeaderboard
    ) -> tuple[AffectionCardPage, ...]:
        chunks = tuple(
            leaderboard.entries[index : index + self.PAGE_SIZE]
            for index in range(0, len(leaderboard.entries), self.PAGE_SIZE)
        ) or ((),)
        page_count = len(chunks)
        pages = tuple(
            self._page(leaderboard, chunk, index + 1, page_count)
            for index, chunk in enumerate(chunks)
        )
        requester_page = next(
            (
                index
                for index, page in enumerate(pages)
                if any(
                    item["is_requester"]
                    for column in page.context["columns"]
                    for item in column
                )
            ),
            0,
        )
        if requester_page == 0:
            return pages
        return (
            (pages[requester_page],)
            + pages[:requester_page]
            + pages[requester_page + 1 :]
        )

    @staticmethod
    def _page(
        leaderboard: AffectionLeaderboard,
        entries: tuple[AffectionLeaderboardEntry, ...],
        page_number: int,
        page_count: int,
    ) -> AffectionCardPage:
        total_count = len(leaderboard.entries)
        layout = (
            "paged"
            if total_count > AffectionCardPresenter.PAGE_SIZE
            else "fixed"
        )
        column_count = 6
        rows = max(1, math.ceil(len(entries) / column_count))
        render_width = 1380
        row_height = 23
        row_gap = 2
        column_gap = 8
        item_font_size = 13
        render_height = (
            164
            + rows * row_height
            + max(0, rows - 1) * row_gap
            + (18 if page_count > 1 else 0)
        )
        columns = tuple(
            tuple(
                item.public_mapping()
                for item in entries[index * rows : (index + 1) * rows]
            )
            for index in range(column_count)
        )
        context = leaderboard.public_context(entries)
        context.update(
            {
                "columns": columns,
                "layout": layout,
                "column_count": column_count,
                "row_height": row_height,
                "row_gap": row_gap,
                "column_gap": column_gap,
                "item_font_size": item_font_size,
                "page_number": page_number,
                "page_count": page_count,
                "render_width": render_width,
                "render_height": render_height,
                "updated_text": datetime.fromtimestamp(
                    leaderboard.updated_at,
                    timezone(timedelta(hours=8)),
                ).strftime("%Y-%m-%d %H:%M"),
            }
        )
        context.pop("entries", None)
        return AffectionCardPage(page_number, page_count, context)


__all__ = (
    "AFFECTION_CARD_TEMPLATE",
    "AffectionCardPage",
    "AffectionCardPresenter",
)
