"""Inbound sticker capture. Hot path never runs vision."""

from __future__ import annotations

from typing import Mapping

from ..contracts import SocialEventEnvelope
from .contracts import CaptureOffer, IngestOutcome, MAX_STICKER_BYTES
from .images import MIME_RULES, mime_matches, sniff_mime
from .lexicon import InvalidStickerAsset, StickerLexicon


_IMAGE_KINDS = frozenset({"image"})
_BLOCKED_KINDS = frozenset({"mface", "face", "video", "file", "forward", "record"})


class StickerCapture:
    def __init__(
        self,
        lexicon: StickerLexicon,
        *,
        enabled: bool = False,
    ) -> None:
        self.lexicon = lexicon
        self.enabled = bool(enabled)

    def consider_bytes(self, offer: CaptureOffer) -> IngestOutcome:
        if not self.enabled:
            return IngestOutcome(None, False, "capture_disabled")
        if offer.segment_kind in _BLOCKED_KINDS or offer.segment_kind not in _IMAGE_KINDS:
            return IngestOutcome(None, False, "segment_kind")
        if offer.actor_id and offer.bot_id and offer.actor_id == offer.bot_id:
            return IngestOutcome(None, False, "self_message")
        mime = str(offer.mime_type or "").strip().casefold()
        if mime not in MIME_RULES or not mime_matches(offer.content, mime):
            return IngestOutcome(None, False, "mime")
        if not offer.content or len(offer.content) > MAX_STICKER_BYTES:
            return IngestOutcome(None, False, "size")
        return self.lexicon.ingest(
            offer.content,
            mime_type=mime,
            origin_kind="group_captured",
            license_status="group_captured",
            now=int(offer.now),
            source_group_id=offer.group_id,
            source_event_id=offer.event_id,
        )

    def consider_event(
        self,
        event: SocialEventEnvelope,
        *,
        now: int,
        files: Mapping[str, bytes] | None = None,
    ) -> tuple[IngestOutcome, ...]:
        if not self.enabled:
            return ()
        group_id = str(event.group_id or "").strip()
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        bot_id = str(payload.get("bot_id") or "").strip()
        actor_id = str(event.actor_id or "").strip()
        if actor_id and bot_id and actor_id == bot_id:
            return ()
        outcomes: list[IngestOutcome] = []
        for item in image_facts(payload):
            content = None
            for key in (item.get("file"), item.get("url"), item.get("path")):
                if key and key in (files or {}):
                    content = files[key]
                    break
            mime = str(item.get("mime_type") or item.get("type") or "")
            if content is None:
                continue
            sniffed = sniff_mime(content)
            if sniffed:
                mime = sniffed
            try:
                outcomes.append(
                    self.consider_bytes(
                        CaptureOffer(
                            content=content,
                            mime_type=mime if mime in MIME_RULES else sniffed,
                            group_id=group_id,
                            event_id=str(event.event_id),
                            actor_id=actor_id,
                            bot_id=bot_id,
                            segment_kind=str(item.get("segment_kind") or "image"),
                            now=int(now),
                        )
                    )
                )
            except InvalidStickerAsset:
                outcomes.append(IngestOutcome(None, False, "invalid_asset"))
        return tuple(outcomes)


def image_facts(payload: Mapping[str, object]) -> tuple[dict[str, str], ...]:
    facts: list[dict[str, str]] = []
    media = payload.get("media")
    if isinstance(media, (list, tuple)):
        for item in media:
            if not isinstance(item, Mapping):
                continue
            kind = str(item.get("type") or "").strip().lower()
            if kind != "image":
                continue
            facts.append(
                {
                    "segment_kind": kind,
                    "file": str(item.get("file") or ""),
                    "path": str(item.get("path") or ""),
                    "url": str(item.get("url") or ""),
                    "mime_type": str(item.get("mime_type") or ""),
                }
            )
    segments = payload.get("segments")
    if isinstance(segments, (list, tuple)):
        for segment in segments:
            if not isinstance(segment, Mapping):
                continue
            kind = str(segment.get("type") or "").strip().lower()
            if kind != "image":
                continue
            data = segment.get("data")
            data_map = data if isinstance(data, Mapping) else {}
            facts.append(
                {
                    "segment_kind": kind,
                    "file": str(data_map.get("file") or ""),
                    "path": str(data_map.get("path") or ""),
                    "url": str(data_map.get("url") or ""),
                    "mime_type": str(data_map.get("mime_type") or ""),
                }
            )
    # Deduplicate identical file/url pairs while preserving order.
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, str]] = []
    for item in facts:
        key = (
            item.get("file") or "",
            item.get("path") or "",
            item.get("url") or "",
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return tuple(unique)


__all__ = ("StickerCapture", "image_facts")
