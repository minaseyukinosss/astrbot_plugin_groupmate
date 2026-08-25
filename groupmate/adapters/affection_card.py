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
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    width: {{ render_width }}px;
    min-height: {{ render_height }}px;
    padding: 18px 16px 20px;
    background: #fff7fa;
    color: #3e2630;
    font-family: Inter, "SF Pro Text", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  main {
    width: 100%;
  }
  header {
    display: flex; align-items: center; gap: 16px;
    min-height: 42px; padding: 0 8px 12px;
  }
  .title { display: flex; flex: 0 0 auto; align-items: center; gap: 12px; }
  .heart {
    display: grid; place-items: center; width: 42px; height: 42px;
    border-radius: 12px; background: #ff5c8d; color: #fff;
    font-family: Arial, sans-serif; font-size: 29px; font-weight: 400;
    box-shadow: 0 4px 12px rgba(176, 22, 79, .12);
  }
  h1 { margin: 0; color: #730d33; font-size: 27px; line-height: 1; font-weight: 850; letter-spacing: .02em; }
  .meta, .updated { margin: 0; color: #896e78; font-size: 13px; white-space: nowrap; }
  .meta { flex: 1; overflow: hidden; text-overflow: ellipsis; }
  .updated { flex: 0 0 auto; margin-left: auto; text-align: right; }
  .mine {
    display: grid; grid-template-columns: auto auto minmax(0, 1fr) auto auto;
    align-items: center; gap: 12px 20px; min-height: 48px;
    margin: 0 8px 10px; padding: 8px 14px;
    background: rgba(255, 255, 255, .72); border: 1.5px solid #ff5c8d; border-radius: 7px;
  }
  .mine-label { padding: 5px 10px; border-radius: 4px; background: #ff5c8d; color: #fff; font-size: 13px; font-weight: 750; }
  .mine-rank { color: #8b173f; font-size: 19px; font-weight: 850; font-variant-numeric: tabular-nums; }
  .mine-name { min-width: 0; overflow: hidden; color: #8b173f; font-size: 18px; font-weight: 800; text-overflow: ellipsis; white-space: nowrap; }
  .you { margin-left: 7px; padding: 2px 6px; border-radius: 999px; background: #ffdae5; color: #b0164f; font-size: 11px; font-style: normal; font-weight: 800; vertical-align: 2px; }
  .mine-stage { color: #7b5d68; font-size: 14px; }
  .mine-score { color: #b0164f; font-size: 18px; font-weight: 850; font-variant-numeric: tabular-nums; }
  .columns {
    display: grid; grid-template-columns: repeat({{ column_count }}, minmax(0, 1fr));
    margin: 0; padding: 0 8px;
  }
  .rank-column { min-width: 0; border-top: 1px solid #f1ccd8; border-right: 1px solid #f1ccd8; border-bottom: 1px solid #f1ccd8; }
  .rank-column:first-child { border-left: 1px solid #f1ccd8; border-radius: 6px 0 0 6px; }
  .rank-column:last-child { border-radius: 0 6px 6px 0; }
  .column-head, .rank-row {
    display: grid; grid-template-columns: 26px minmax(0, 1fr) 50px 38px;
    align-items: center; gap: 6px;
  }
  .column-head {
    min-height: 27px; padding: 4px 7px; background: #fff0f5; color: #8d6675;
    border-bottom: 1px solid #efc5d3; font-size: 10px; font-weight: 750;
  }
  .column-head span:nth-child(1), .column-head span:nth-child(3) { text-align: right; }
  .rank-row {
    position: relative; min-height: 29px; padding: 3px 7px;
    border-bottom: 1px solid #f6e1e8; background: rgba(255, 255, 255, .54); font-size: 12px;
  }
  .cols-4 .rank-row, .cols-5 .rank-row { min-height: 21px; padding-top: 1px; padding-bottom: 1px; font-size: 11px; }
  .cols-4 .column-head, .cols-5 .column-head { min-height: 25px; padding-top: 3px; padding-bottom: 3px; font-size: 10px; }
  .rank-row:last-child { border-bottom: 0; }
  .rank-row.me { z-index: 1; margin: -1px 3px 0; border: 1.5px solid #ff5c8d; border-radius: 5px; background: #fff9fb; }
  .rank { color: #5f4b53; font-weight: 750; text-align: right; font-variant-numeric: tabular-nums; }
  .rank.top { color: #b0164f; }
  .identity { display: flex; min-width: 0; align-items: baseline; gap: 5px; }
  .name { min-width: 0; overflow: hidden; color: #30232a; font-weight: 700; text-overflow: ellipsis; white-space: nowrap; }
  .tail { flex: 0 0 auto; color: #a08c94; font-size: .86em; font-variant-numeric: tabular-nums; }
  .stage { color: #725d65; font-size: .9em; white-space: nowrap; }
  .score { text-align: right; color: #5e5056; font-weight: 800; font-variant-numeric: tabular-nums; }
  .score.positive { color: #df2c65; }
  .score.negative { color: #df453c; }
  footer { margin: 8px 10px 0; color: #9b7c88; font-size: 11px; text-align: right; }
</style>
</head>
<body>
<main>
  <header>
    <div class="title">
      <span class="heart">♡</span>
      <h1>好感度</h1>
    </div>
    <p class="meta">{{ group_name|e }}　·　{{ group_id|e }}　·　近 30 天活跃 {{ active_count }} 人</p>
    <p class="updated">更新于 {{ updated_text|e }}</p>
  </header>
  <section class="mine">
    <span class="mine-label">我的位置</span>
    <span class="mine-rank">{{ requester.rank }} 名</span>
    <span class="mine-name">{{ requester.display_name|e }}<b class="you">你</b></span>
    <strong class="mine-score">{{ requester.score_text|e }}</strong>
    <span class="mine-stage">{{ requester.stage|e }}</span>
  </section>
  <div class="columns cols-{{ column_count }}">
  {% for column in columns %}
    <section class="rank-column">
      <div class="column-head"><span>排名</span><span>昵称（QQ末四位）</span><span>好感度</span><span>阶段</span></div>
    {% for item in column %}
      <div class="rank-row{% if item.is_requester %} me{% endif %}">
        <span class="rank{% if item.rank <= 3 %} top{% endif %}">{{ item.rank }}</span>
        <span class="identity"><span class="name">{{ item.display_name|e }}</span><span class="tail">{{ item.platform_tail|e }}</span>{% if item.is_requester %}<b class="you">你</b>{% endif %}</span>
        <strong class="score {{ item.score_tone|e }}">{{ item.score_text|e }}</strong>
        <span class="stage">{{ item.stage|e }}</span>
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
        return (pages[requester_page],) + pages[:requester_page] + pages[requester_page + 1 :]

    @staticmethod
    def _page(
        leaderboard: AffectionLeaderboard,
        entries: tuple[AffectionLeaderboardEntry, ...],
        page_number: int,
        page_count: int,
    ) -> AffectionCardPage:
        entry_count = len(entries)
        column_count = (
            5
            if entry_count >= 161
            else 4
            if entry_count >= 97
            else 3
            if entry_count >= 49
            else 2
            if entry_count >= 17
            else 1
        )
        rows = max(1, math.ceil(len(entries) / column_count))
        render_width = (920, 1080, 1240, 1300, 1340)[column_count - 1]
        row_height = (29, 26, 23, 21, 21)[column_count - 1]
        render_height = 179 + rows * row_height + (18 if page_count > 1 else 0)
        columns = tuple(
            tuple(item.public_mapping() for item in entries[index : index + rows])
            for index in range(0, len(entries), rows)
        )
        context = leaderboard.public_context(entries)
        context.update(
            {
                "columns": columns,
                "column_count": column_count,
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
