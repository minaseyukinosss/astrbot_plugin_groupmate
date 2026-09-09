"""Immutable sticker-lexicon contracts. Vision models stay out of this layer."""

from __future__ import annotations

from dataclasses import dataclass


STICKER_STATUSES = frozenset({"candidate", "ready", "disabled", "rejected"})
STICKER_ORIGINS = frozenset({"admin_import", "group_captured"})
STICKER_LICENSES = frozenset(
    {"owned", "licensed", "public_domain", "cc0", "cc_by", "group_captured"}
)
STICKER_ATTITUDES = frozenset(
    {
        "amused",
        "tease",
        "helpless",
        "confused",
        "startle",
        "wronged",
        "refuse",
        "agree",
        "acknowledge",
        "warm",
        "close",
        "proud",
    }
)
STICKER_AFFECTION_FLOORS = (
    (0, "anyone", "谁都行"),
    (10, "knows", "至少认识"),
    (30, "familiar", "至少熟悉"),
    (55, "close", "至少亲近"),
    (80, "in_sync", "仅默契"),
)
CAPTION_SOURCES = frozenset({"", "admin", "vision", "mixed"})
CANDIDATE_POOL_LIMIT = 30
LIBRARY_LIMIT = 200
MEANING_LIMIT = 48
PHRASE_LIMIT = 3
DEFAULT_COOLDOWN_SECONDS = 120
MAX_STICKER_BYTES = 2 * 1024 * 1024
STICKER_PACK_LIMIT = 15


_AFFECTION_FLOOR_ALIASES = {
    "0": 0,
    "anyone": 0,
    "谁都行": 0,
    "陌生": 0,
    "stranger": 0,
    "10": 10,
    "knows": 10,
    "认识": 10,
    "至少认识": 10,
    "30": 30,
    "familiar": 30,
    "熟悉": 30,
    "至少熟悉": 30,
    "55": 55,
    "close": 55,
    "亲近": 55,
    "至少亲近": 55,
    "80": 80,
    "in_sync": 80,
    "默契": 80,
    "仅默契": 80,
}


def snap_affection_floor(value: object) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    if number <= 0:
        return 0
    chosen = 0
    for floor, _key, _label in STICKER_AFFECTION_FLOORS:
        if number >= floor:
            chosen = floor
    return chosen


def parse_affection_floor(value: object) -> int:
    if isinstance(value, str):
        key = value.strip()
        folded = key.casefold()
        if key in _AFFECTION_FLOOR_ALIASES:
            return _AFFECTION_FLOOR_ALIASES[key]
        if folded in _AFFECTION_FLOOR_ALIASES:
            return _AFFECTION_FLOOR_ALIASES[folded]
    return snap_affection_floor(value)


def affection_floor_label(value: object) -> str:
    floor = snap_affection_floor(value)
    for stored, _key, label in STICKER_AFFECTION_FLOORS:
        if stored == floor:
            return label
    return "谁都行"


@dataclass(frozen=True)
class StickerCard:
    asset_id: str
    sha256: str
    mime_type: str
    size_bytes: int
    relative_path: str
    origin_kind: str
    license_status: str
    source_group_id: str | None
    source_event_id: str | None
    status: str
    meaning: str
    use_when: tuple[str, ...]
    do_not_use: tuple[str, ...]
    attitudes: tuple[str, ...]
    intensity: int
    min_familiarity: int
    max_boundary_pressure: int
    caption_source: str
    is_sticker_judgment: str
    judgment_reason: str
    sighting_count: int
    use_count: int
    last_used_at: int | None
    last_used_group_id: str | None
    cooldown_seconds: int
    created_at: int
    updated_at: int

    def meaning_formed(self) -> bool:
        return bool(self.meaning.strip())

    def sendable(self) -> bool:
        return self.status == "ready" and self.meaning_formed()


@dataclass(frozen=True)
class IngestOutcome:
    card: StickerCard | None
    created: bool
    reason: str


@dataclass(frozen=True)
class AccompanimentDecision:
    asset_id: str | None
    media_path: str | None
    reason: str


@dataclass(frozen=True)
class StickerGift:
    items: tuple[tuple[str, str], ...]
    pack: bool
    reason: str
    asked: bool


@dataclass(frozen=True)
class CaptureOffer:
    content: bytes
    mime_type: str
    group_id: str
    event_id: str
    actor_id: str
    bot_id: str
    segment_kind: str
    now: int


__all__ = (
    "AccompanimentDecision",
    "CANDIDATE_POOL_LIMIT",
    "CAPTION_SOURCES",
    "CaptureOffer",
    "DEFAULT_COOLDOWN_SECONDS",
    "IngestOutcome",
    "LIBRARY_LIMIT",
    "MAX_STICKER_BYTES",
    "MEANING_LIMIT",
    "PHRASE_LIMIT",
    "STICKER_ATTITUDES",
    "STICKER_LICENSES",
    "STICKER_ORIGINS",
    "STICKER_PACK_LIMIT",
    "STICKER_STATUSES",
    "StickerCard",
    "StickerGift",
    "affection_floor_label",
    "parse_affection_floor",
    "snap_affection_floor",
)
