"""Deterministic, scene-conditional local reaction media selection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple

from ..capabilities.contracts import MediaCandidate
from ..core.response_act import ResponseAct
from ..models import InteractionScene


_MEDIA_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")
_ALLOWED_ACTS = frozenset(
    {
        ResponseAct.RECIPROCATE,
        ResponseAct.PLAYFUL_REPLY,
        ResponseAct.VISUAL_REACTION,
    }
)
_ALLOWED_SCENES = frozenset(
    {
        InteractionScene.SOCIAL_RESPONSE,
        InteractionScene.DIRECT_ADDRESS,
        InteractionScene.REPLY_TO_BOT,
        InteractionScene.ACTIVE_CONTINUATION,
    }
)


@dataclass(frozen=True)
class ReactionAsset:
    media_id: str
    relative_path: str
    tags: Tuple[str, ...]
    safe: bool

    def __post_init__(self) -> None:
        media_id = str(self.media_id or "").strip()
        if not _MEDIA_ID.match(media_id):
            raise ValueError("media_id must be a stable identifier")
        relative_path = str(self.relative_path or "").strip()
        if not relative_path or Path(relative_path).is_absolute():
            raise ValueError("relative_path must be a non-empty relative path")
        tags = _clean_tags(self.tags)
        if not tags:
            raise ValueError("reaction asset tags are required")
        if not isinstance(self.safe, bool):
            raise TypeError("reaction asset safe must be a bool")
        object.__setattr__(self, "media_id", media_id)
        object.__setattr__(self, "relative_path", relative_path)
        object.__setattr__(self, "tags", tags)


class ReactionPolicy:
    def allowed(
        self,
        act: ResponseAct,
        scene: InteractionScene,
        ambiguous: bool,
    ) -> bool:
        return (
            not bool(ambiguous)
            and act in _ALLOWED_ACTS
            and scene in _ALLOWED_SCENES
        )


class LocalReactionCatalog:
    def __init__(self, root: Path, items: Iterable[ReactionAsset]) -> None:
        self.root = Path(root).expanduser().resolve()
        self._items = tuple(items)
        if not all(isinstance(item, ReactionAsset) for item in self._items):
            raise TypeError("reaction catalog items must be ReactionAsset values")

    @classmethod
    def from_items(
        cls,
        root: Path,
        items: Iterable[ReactionAsset],
    ) -> "LocalReactionCatalog":
        return cls(root, items)

    def select(
        self,
        required_tags: Sequence[str],
        recent_ids: Sequence[str],
    ) -> Optional[MediaCandidate]:
        required = frozenset(_clean_tags(required_tags))
        recent = {
            str(media_id or "").strip()
            for media_id in (recent_ids or ())
            if str(media_id or "").strip()
        }
        eligible = []
        for item in self._items:
            if not item.safe or item.media_id in recent:
                continue
            if not required.issubset(frozenset(item.tags)):
                continue
            media_path = self._safe_media_path(item.relative_path)
            if media_path is None:
                continue
            eligible.append((item, media_path))
        if not eligible:
            return None
        asset, media_path = sorted(
            eligible, key=lambda entry: entry[0].media_id
        )[0]
        return MediaCandidate(
            media_id=asset.media_id,
            source="local_reaction_catalog",
            locator=str(media_path),
            media_kind="image",
            semantic_label=",".join(asset.tags),
            purpose="decorative_reaction",
            safety_label="catalog_approved",
        )

    def _safe_media_path(self, relative_path: str) -> Optional[Path]:
        candidate = (self.root / relative_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return None
        if not candidate.is_file():
            return None
        return candidate


def _clean_tags(values: Sequence[str]) -> Tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError("reaction tags must be a sequence")
    result = []
    for value in values or ():
        tag = " ".join(str(value or "").split()).casefold()
        if tag and tag not in result:
            result.append(tag)
    return tuple(result)
