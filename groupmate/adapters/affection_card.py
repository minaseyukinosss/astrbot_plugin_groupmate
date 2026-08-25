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
<style>
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    width: 1280px;
    padding: 30px;
    background: #f8e8ee;
    color: #3f2731;
    font-family: Inter, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  }
  main {
    padding: 30px 32px 34px;
    background: #fffafb;
    border: 1px solid #e8ccd6;
    border-radius: 16px;
  }
  header { display: flex; align-items: center; justify-content: space-between; gap: 24px; }
  .title { display: flex; align-items: center; gap: 14px; }
  .heart {
    display: grid; place-items: center; width: 50px; height: 50px;
    border-radius: 13px; background: #9d365d; color: #fff; font-size: 29px;
  }
  h1 { margin: 0; color: #702845; font-size: 30px; line-height: 1.2; }
  .meta, .updated { margin: 6px 0 0; color: #876b76; font-size: 15px; }
  .updated { margin: 0; text-align: right; }
  .mine {
    display: grid; grid-template-columns: auto minmax(0, 1fr) auto auto;
    align-items: center; gap: 12px 18px; margin: 25px 0 18px; padding: 15px 18px;
    background: #f3d7e1; border-radius: 11px;
  }
  .mine-label { color: #7f3652; font-size: 14px; font-weight: 700; }
  .mine-name { min-width: 0; overflow: hidden; font-size: 19px; font-weight: 750; text-overflow: ellipsis; white-space: nowrap; }
  .you { margin-left: 8px; padding: 2px 7px; border-radius: 999px; background: #9d365d; color: #fff; font-size: 12px; }
  .mine-stage { color: #76545f; font-size: 15px; }
  .mine-score { color: #872b50; font-size: 22px; font-weight: 800; font-variant-numeric: tabular-nums; }
  .columns { display: grid; grid-template-columns: repeat({{ column_count }}, minmax(0, 1fr)); gap: 0 16px; }
  .rank-column { min-width: 0; }
  .rank-row {
    display: grid; grid-template-columns: 34px minmax(0, 1fr) 54px 68px 48px;
    align-items: center; min-height: 37px; gap: 8px; padding: 5px 8px;
    border-bottom: 1px solid #f0dfe5; font-size: 14px;
  }
  .rank-row.me { background: #f3d7e1; border-radius: 7px; border-bottom-color: transparent; }
  .rank { color: #a06a7f; font-weight: 700; text-align: right; font-variant-numeric: tabular-nums; }
  .rank.top { color: #812a4c; font-size: 17px; }
  .name { min-width: 0; overflow: hidden; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
  .tail { color: #a38993; font-size: 12px; font-variant-numeric: tabular-nums; }
  .stage { color: #76545f; font-size: 12px; white-space: nowrap; }
  .score { text-align: right; color: #8a7b81; font-weight: 750; font-variant-numeric: tabular-nums; }
  .score.positive { color: #872b50; }
  .score.negative { color: #a3483e; }
  footer { margin-top: 16px; color: #9b7c88; font-size: 12px; text-align: right; }
</style>
</head>
<body>
<main>
  <header>
    <div class="title">
      <span class="heart">♥</span>
      <div><h1>好感度</h1><p class="meta">{{ group_name|e }} · 群 {{ group_id|e }} · 近 30 天活跃 {{ active_count }} 人</p></div>
    </div>
    <p class="updated">更新于<br>{{ updated_text|e }}</p>
  </header>
  <section class="mine">
    <span class="mine-label">我的位置 · 第 {{ requester.rank }} 名</span>
    <span class="mine-name">{{ requester.display_name|e }}<b class="you">你</b></span>
    <span class="mine-stage">{{ requester.stage|e }}</span>
    <strong class="mine-score">{{ requester.score_text|e }}</strong>
  </section>
  <div class="columns">
  {% for column in columns %}
    <section class="rank-column">
    {% for item in column %}
      <div class="rank-row{% if item.is_requester %} me{% endif %}">
        <span class="rank{% if item.rank <= 3 %} top{% endif %}">{{ item.rank }}</span>
        <span class="name">{{ item.display_name|e }}{% if item.is_requester %}<b class="you">你</b>{% endif %}</span>
        <span class="tail">{{ item.platform_tail|e }}</span>
        <span class="stage">{{ item.stage|e }}</span>
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
        return (pages[requester_page],) + pages[:requester_page] + pages[requester_page + 1 :]

    @staticmethod
    def _page(
        leaderboard: AffectionLeaderboard,
        entries: tuple[AffectionLeaderboardEntry, ...],
        page_number: int,
        page_count: int,
    ) -> AffectionCardPage:
        column_count = 4 if len(entries) >= 80 else 3 if len(entries) >= 36 else 2 if len(entries) >= 16 else 1
        rows = max(1, math.ceil(len(entries) / column_count))
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
