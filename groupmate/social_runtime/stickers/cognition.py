"""Meaning quality gates. Empty or generic captions cannot be sent."""

from __future__ import annotations

import re

from .contracts import MEANING_LIMIT, PHRASE_LIMIT, STICKER_ATTITUDES


UNFORMED_MEANING = "认知尚未形成"
_GENERIC = frozenset(
    {
        "可爱",
        "有趣",
        "表情包",
        "一张图",
        "图片",
        "搞笑",
        "好玩",
        "梗图",
        "表情",
        "好看",
        "有意思",
    }
)
_URL = re.compile(r"https?://", re.IGNORECASE)
_LONG_NUMBER = re.compile(r"(?<!\d)\d{8,}(?!\d)")
_PATH = re.compile(r"(/Users/|[A-Za-z]:\\|/home/|/tmp/)")


class InvalidStickerMeaning(ValueError):
    """Raised when a caption cannot be stored as a sendable meaning."""


def normalize_meaning(value: object) -> str:
    text = str(value or "").strip()
    if len(text) > MEANING_LIMIT:
        text = text[:MEANING_LIMIT].rstrip()
    return text


def meaning_is_sendable(value: object) -> bool:
    text = normalize_meaning(value)
    if len(text) < 2:
        return False
    compact = re.sub(r"[\s，。！？,.!]+", "", text)
    if compact in _GENERIC or text in _GENERIC:
        return False
    if _URL.search(text) or _LONG_NUMBER.search(text) or _PATH.search(text):
        return False
    return True


def display_meaning(value: object) -> str:
    text = normalize_meaning(value)
    return text if meaning_is_sendable(text) else UNFORMED_MEANING


def normalize_phrases(values: object) -> tuple[str, ...]:
    source = values if isinstance(values, (list, tuple)) else ()
    phrases: list[str] = []
    for item in source:
        text = str(item or "").strip()
        if not text or text in phrases:
            continue
        phrases.append(text[:MEANING_LIMIT])
        if len(phrases) >= PHRASE_LIMIT:
            break
    return tuple(phrases)


def normalize_attitudes(values: object) -> tuple[str, ...]:
    source = values if isinstance(values, (list, tuple)) else ()
    seen: list[str] = []
    for item in source:
        name = str(item or "").strip().casefold()
        if name in STICKER_ATTITUDES and name not in seen:
            seen.append(name)
    return tuple(seen)


def require_sendable_meaning(value: object) -> str:
    text = normalize_meaning(value)
    if not meaning_is_sendable(text):
        raise InvalidStickerMeaning("sticker meaning is empty or too generic")
    return text


__all__ = (
    "InvalidStickerMeaning",
    "UNFORMED_MEANING",
    "display_meaning",
    "meaning_is_sendable",
    "normalize_attitudes",
    "normalize_meaning",
    "normalize_phrases",
    "require_sendable_meaning",
)
