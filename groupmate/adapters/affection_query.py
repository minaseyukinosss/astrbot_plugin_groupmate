"""Exact command matching and presentation contract for affection queries."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from ..social_runtime.society.affection_leaderboard import AffectionLeaderboard
from .affection_card import AffectionCardPage


def is_affection_query(text: object) -> bool:
    """Claim only the explicit public leaderboard command."""

    normalized = unicodedata.normalize("NFKC", str(text or "")).strip()
    return normalized in {"查看好感度", "查询好感度"}


@dataclass(frozen=True)
class AffectionQuery:
    leaderboard: AffectionLeaderboard
    pages: tuple[AffectionCardPage, ...]

    @property
    def text_fallback(self) -> str:
        return self.leaderboard.text_fallback()


__all__ = ("AffectionQuery", "is_affection_query")
