"""Immutable contracts for governed persona media."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MediaAsset:
    asset_id: str
    source: str
    license_status: str
    mime_type: str
    size_bytes: int
    sha256: str
    relative_path: str
    semantic_tags: tuple[str, ...]
    emotion_tags: tuple[str, ...]
    act_tags: tuple[str, ...]
    allowed_modes: tuple[str, ...]
    min_familiarity: int
    max_boundary_pressure: int
    intensity: int
    enabled: bool
    cooldown_seconds: int
    culture_tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class MediaUse:
    asset_id: str
    used_at: int


@dataclass(frozen=True)
class MediaSelectionContext:
    now: int
    semantic_tags: tuple[str, ...]
    emotion_tags: tuple[str, ...]
    act_tags: tuple[str, ...]
    mode: str
    familiarity: int
    boundary_pressure: int
    culture_tags: tuple[str, ...]
    recent_uses: tuple[MediaUse, ...]
    text_sufficient: bool


@dataclass(frozen=True)
class MediaSelection:
    selected_asset_id: str | None
    reason_codes: tuple[str, ...]


__all__ = ("MediaAsset", "MediaSelection", "MediaSelectionContext", "MediaUse")
