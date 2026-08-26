"""Exact local commands for a member's own profile."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class ProfileCommand:
    kind: str
    fact_number: int | None = None
    content: str | None = None


@dataclass(frozen=True)
class ProfileQueryResult:
    text: str


_CORRECT = re.compile(r"^纠正画像\s+([1-9]\d*)\s+(.+)$")
_DELETE = re.compile(r"^删除画像\s+([1-9]\d*)$")


def parse_profile_command(text: object) -> ProfileCommand | None:
    normalized = unicodedata.normalize("NFKC", str(text or "")).strip()
    if normalized in {"查看我的画像", "我的画像"}:
        return ProfileCommand("show_self")
    if normalized == "停止画像个性化":
        return ProfileCommand("disable_personalization")
    if normalized == "恢复画像个性化":
        return ProfileCommand("enable_personalization")
    match = _CORRECT.fullmatch(normalized)
    if match is not None:
        content = " ".join(match.group(2).split())
        if 1 <= len(content) <= 160:
            return ProfileCommand(
                "correct_fact", int(match.group(1)), content
            )
        return None
    match = _DELETE.fullmatch(normalized)
    if match is not None:
        return ProfileCommand("delete_fact", int(match.group(1)))
    return None


__all__ = ("ProfileCommand", "ProfileQueryResult", "parse_profile_command")
