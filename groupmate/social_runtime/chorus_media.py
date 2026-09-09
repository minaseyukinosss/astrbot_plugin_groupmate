"""Hash-addressed files for sticker chorus. Independent of the sticker lexicon."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .contracts import SocialEventEnvelope
from .stickers.images import extension_for, looks_like_sticker_file, mime_matches, sniff_mime


STICKER_CHORUS_PREFIX = "sticker:"
_STICKER_PAYLOAD = re.compile(r"^sticker:([0-9a-f]{64})$")
_BLOCKED_KINDS = frozenset({"mface", "face", "video", "file", "forward", "record", "at"})
_SKIP_KINDS = frozenset({"reply"})


def sticker_chorus_payload(sha256: str) -> str:
    digest = str(sha256 or "").strip().casefold()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("sticker chorus identity requires a SHA-256 digest")
    return f"{STICKER_CHORUS_PREFIX}{digest}"


def sticker_chorus_digest(payload: str) -> str | None:
    match = _STICKER_PAYLOAD.fullmatch(str(payload or "").strip())
    return None if match is None else match.group(1)


class UnsafeChorusMediaPath(ValueError):
    """Raised when a chorus file would leave the plugin data directory."""


class ChorusMediaStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.files_dir = self.data_dir / "persona_media" / "chorus" / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)

    def ingest(self, content: bytes, mime_type: str) -> tuple[str, Path]:
        payload = bytes(content or b"")
        mime = str(mime_type or "").strip().casefold()
        sniffed = sniff_mime(payload)
        if sniffed:
            mime = sniffed
        if not mime or not mime_matches(payload, mime):
            raise ValueError("chorus media MIME or magic does not match")
        if not looks_like_sticker_file(payload, mime):
            raise ValueError("chorus media does not look like a sticker")
        digest = hashlib.sha256(payload).hexdigest()
        relative = Path("persona_media") / "chorus" / "files" / f"{digest}{extension_for(mime)}"
        target = self._registered_file_path(relative)
        if not target.exists():
            target.write_bytes(payload)
        return digest, target

    def resolve(self, sha256: str) -> Path | None:
        digest = str(sha256 or "").strip().casefold()
        if len(digest) != 64:
            return None
        matches = tuple(self.files_dir.glob(f"{digest}.*"))
        if len(matches) != 1:
            return None
        try:
            self._registered_file_path(
                Path("persona_media") / "chorus" / "files" / matches[0].name
            )
        except UnsafeChorusMediaPath:
            return None
        return matches[0] if matches[0].is_file() else None

    def _registered_file_path(self, relative: Path) -> Path:
        if relative.is_absolute() or relative.parts[:3] != ("persona_media", "chorus", "files"):
            raise UnsafeChorusMediaPath("chorus media must be stored in persona_media/chorus/files")
        if len(relative.parts) != 4:
            raise UnsafeChorusMediaPath("chorus filename must be a single path segment")
        target = (self.data_dir / relative).resolve()
        if not target.is_relative_to(self.data_dir) or target.parent != self.files_dir:
            raise UnsafeChorusMediaPath("chorus path is outside the chorus directory")
        return target


def attach_sticker_chorus_media(
    event: SocialEventEnvelope,
    *,
    files: Mapping[str, bytes],
    store: ChorusMediaStore,
) -> SocialEventEnvelope:
    """Stamp a custom sticker with its content hash. Photos and mall faces stay untouched."""

    payload = dict(event.payload)
    if isinstance(payload.get("chorus_media"), Mapping):
        return event
    offer = _sticker_chorus_offer(payload, files)
    if offer is None:
        return event
    try:
        digest, _path = store.ingest(offer.content, offer.mime_type)
    except ValueError:
        return event
    payload["chorus_media"] = {
        "sha256": digest,
        "mime_type": offer.mime_type,
    }
    values = event.to_dict()
    values["payload"] = payload
    return SocialEventEnvelope.create(**values)


@dataclass(frozen=True)
class _StickerOffer:
    content: bytes
    mime_type: str


def _sticker_chorus_offer(
    payload: Mapping[str, object], files: Mapping[str, bytes]
) -> _StickerOffer | None:
    kinds: list[str] = []
    keys: list[str] = []
    segments = payload.get("segments")
    if isinstance(segments, (list, tuple)):
        for segment in segments:
            if not isinstance(segment, Mapping):
                continue
            kind = str(segment.get("type") or "").strip().lower()
            data = segment.get("data")
            data_map = data if isinstance(data, Mapping) else {}
            if kind in _SKIP_KINDS:
                continue
            if kind == "text" and not str(data_map.get("text") or "").strip():
                continue
            if kind in _BLOCKED_KINDS:
                return None
            kinds.append(kind)
            if kind == "image":
                for field in ("file", "path", "url"):
                    value = str(data_map.get(field) or "").strip()
                    if value:
                        keys.append(value)
    if kinds != ["image"]:
        return None
    unique: list[bytes] = []
    for key in keys:
        content = files.get(key)
        if not content or content in unique:
            continue
        unique.append(content)
    if len(unique) != 1:
        return None
    content = unique[0]
    mime = sniff_mime(content)
    if not mime or not looks_like_sticker_file(content, mime):
        return None
    return _StickerOffer(content, mime)


__all__ = (
    "ChorusMediaStore",
    "STICKER_CHORUS_PREFIX",
    "UnsafeChorusMediaPath",
    "attach_sticker_chorus_media",
    "sticker_chorus_digest",
    "sticker_chorus_payload",
)
