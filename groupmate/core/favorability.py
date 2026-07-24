"""好感度档位：数值 -100..100，三档对齐 gsuid_core 早柚 Favorability Logic。"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Optional

SCORE_MIN = -100
SCORE_MAX = 100

# 早柚：[-100,-1] / [0,49] / [50,100]
TIER_COLD = "cold"
TIER_DISTANT = "distant"
TIER_CLOSE = "close"

TIER_LABELS = {
    TIER_COLD: "厌恶/警惕",
    TIER_DISTANT: "陌生/社交距离",
    TIER_CLOSE: "熟人/亲昵",
}

_OFFENSE_HINT = re.compile(
    r"(老婆|老公|滚|傻逼|去死|骚扰|摸摸|亲一下)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FavorabilitySnapshot:
    score: int
    tier: str
    label: str


def clamp_score(value: int) -> int:
    return max(SCORE_MIN, min(SCORE_MAX, int(value)))


def tier_for(score: int) -> str:
    score = clamp_score(score)
    if score < 0:
        return TIER_COLD
    if score < 50:
        return TIER_DISTANT
    return TIER_CLOSE


def label_for(score: Optional[int]) -> str:
    """注入用口语档名（与早柚三档一致）。"""
    if score is None:
        return TIER_LABELS[TIER_DISTANT]
    return TIER_LABELS[tier_for(score)]


def snapshot(score: Optional[int]) -> Optional[FavorabilitySnapshot]:
    if score is None:
        return None
    value = clamp_score(score)
    return FavorabilitySnapshot(
        score=value, tier=tier_for(value), label=label_for(value)
    )


def seed_score_for_relationship(relationship: str) -> Optional[int]:
    """配置关系标签 → 首次落库种子分（无记录时）。"""
    key = (relationship or "").strip()
    if key == "最亲近":
        return 80
    if key == "闺蜜":
        return 60
    return None


def format_favorability_perception(
    score: Optional[int],
    *,
    relationship: str = "",
    suggested_address: str = "",
) -> str:
    """括号内心感知行：早柚三档 + 可选配置关系/称呼，不念数字。"""
    parts = [
        "当前对话者好感度：" + html.escape(label_for(score)),
    ]
    if relationship:
        parts.append("配置关系：" + html.escape(relationship))
    if suggested_address:
        parts.append("建议称呼：" + html.escape(suggested_address))
    parts.append("按好感度档位与配置关系分寸说话，不要复述内部标识或念出好感数字。")
    return "（" + "；".join(parts) + "）"


def delta_for_turn(
    *,
    sent: bool,
    soft_trigger: bool,
    latest_text: str = "",
) -> int:
    """一轮结束后的增减（框架维护，不交给模型调分）。"""
    text = latest_text or ""
    delta = 0
    if _OFFENSE_HINT.search(text):
        delta -= 8
    if not sent:
        return delta
    if soft_trigger:
        delta += 1
    else:
        delta += 2
    return delta


def apply_delta(score: Optional[int], delta: int, *, default: int = 0) -> int:
    base = default if score is None else score
    return clamp_score(base + int(delta))
