"""Heuristic gate: when @ wakes need realtime / external facts, defer to AstrBot."""

from __future__ import annotations

import re


_EXPLICIT_SEARCH = re.compile(
    r"(?:搜索|搜一下|查一下|帮我查|联网|google|百度一下)",
    re.IGNORECASE,
)
_TIMELY_OR_PUBLIC = re.compile(
    r"(?:热搜|新闻|最新|怎么了|是真的吗|真的假的|"
    r"为什么.{0,24}(?:骂|黑|争议|翻车|出轨|塌房)|"
    r"(?:骂|黑|争议|翻车|塌房).{0,12}(?:她|他|他们|她们))",
)
_URL = re.compile(r"https?://", re.IGNORECASE)


def needs_external_knowledge(text: str) -> bool:
    """Return True when the utterance likely needs web search / external facts.

    Prefer false positives (hand off to AstrBot Agent) over Groupmate inventing
    current events without tools.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    if _URL.search(cleaned):
        return True
    if _EXPLICIT_SEARCH.search(cleaned):
        return True
    if _TIMELY_OR_PUBLIC.search(cleaned):
        return True
    return False
