"""情绪键：描述文案来自 Pack，状态不进 system。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Mapping, Optional

MOOD_NEUTRAL = "neutral"
MOOD_SOFT = "soft"
MOOD_GUARDED = "guarded"
MOOD_BRIGHT = "bright"

_DEFAULT_DESCRIPTIONS: Dict[str, str] = {
    MOOD_NEUTRAL: "心情平常，放松地待在群里",
    MOOD_SOFT: "语气更柔软一点，愿意体贴对方",
    MOOD_GUARDED: "有点警惕，边界感起来了，但还没撕破脸",
    MOOD_BRIGHT: "心情不错，轻快想接话",
}

_OFFENSE_HINT = re.compile(
    r"(老婆|老公|滚|傻逼|去死|骚扰|摸摸|亲一下)",
    re.IGNORECASE,
)


def load_mood_descriptions(pack_dir: Path) -> Dict[str, str]:
    path = Path(pack_dir) / "moods.md"
    result = dict(_DEFAULT_DESCRIPTIONS)
    if not path.exists():
        return result
    current_key = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("## "):
            current_key = line[3:].strip().lower()
            continue
        if current_key and line and not line.startswith("#"):
            result[current_key] = line
            current_key = ""
    return result


def describe_mood(
    mood_key: str,
    descriptions: Optional[Mapping[str, str]] = None,
) -> str:
    mapping = descriptions if descriptions is not None else _DEFAULT_DESCRIPTIONS
    key = (mood_key or MOOD_NEUTRAL).strip().lower() or MOOD_NEUTRAL
    text = mapping.get(key) or mapping.get(MOOD_NEUTRAL) or _DEFAULT_DESCRIPTIONS[MOOD_NEUTRAL]
    return f"（{text}。）"


def infer_mood(
    *,
    soft_trigger: bool,
    latest_text: str = "",
    relationship: str = "",
    favorability: Optional[int] = None,
) -> str:
    text = latest_text or ""
    if _OFFENSE_HINT.search(text):
        return MOOD_GUARDED
    if favorability is not None:
        from .favorability import TIER_CLOSE, TIER_COLD, tier_for

        tier = tier_for(favorability)
        if tier == TIER_COLD:
            return MOOD_GUARDED
        if tier == TIER_CLOSE and not soft_trigger:
            return MOOD_SOFT
    if soft_trigger:
        return MOOD_NEUTRAL
    if relationship in ("最亲近", "闺蜜"):
        return MOOD_SOFT
    if "夸" in text or "可爱" in text or "厉害" in text:
        return MOOD_BRIGHT
    return MOOD_NEUTRAL
