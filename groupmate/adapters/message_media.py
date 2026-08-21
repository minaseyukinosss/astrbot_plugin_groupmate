"""Private message-media sources and privacy-trimmed public message parts."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import ipaddress
import json
import re
import socket
import urllib.parse
import urllib.request
from pathlib import Path, PurePath
from typing import Awaitable, Callable, Mapping

from ..social_runtime.contracts import SocialEventEnvelope
from ..social_runtime.persistence.schema import connect_database, initialize_database


_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{6,}(?!\d)")
_LABELS = {
    "image": "图片",
    "record": "语音",
    "video": "视频",
    "file": "文件",
    "face": "QQ 表情",
    "mface": "商城表情",
    "forward": "合并转发",
    "json": "卡片消息",
    "share": "链接分享",
    "location": "位置",
    "music": "音乐分享",
    "reply": "回复",
    "at": "@成员",
}
_PREVIEW_KINDS = {
    "image": "image",
    "record": "audio",
    "video": "video",
}
_SOURCE_KEYS = ("url", "file", "path", "thumb")
_MIME_TYPES = {
    "image": {"image/png", "image/jpeg", "image/webp", "image/gif", "image/avif"},
    "audio": {"audio/mpeg", "audio/ogg", "audio/wav", "audio/x-wav", "audio/aac", "audio/mp4", "audio/amr"},
    "video": {"video/mp4", "video/webm", "video/quicktime"},
}
_MAX_BYTES = {
    "image": 4 * 1024 * 1024,
    "audio": 8 * 1024 * 1024,
    "video": 12 * 1024 * 1024,
}
MediaFetcher = Callable[
    [str, int],
    Awaitable[tuple[bytes, str]] | tuple[bytes, str],
]


class MessageMediaDirectory:
    """Keeps raw OneBot media sources private and exposes opaque references."""

    def __init__(
        self,
        path: Path,
        cache_dir: Path,
        *,
        fetcher: MediaFetcher | None = None,
    ) -> None:
        self.path = Path(path)
        self.cache_dir = Path(cache_dir)
        self.fetcher = fetcher or self._fetch_url
        initialize_database(self.path)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_table()

    def contains(self, media_ref: str, *, persona_id: str, group_id: str) -> bool:
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT 1 FROM message_media WHERE media_ref=? AND persona_id=? AND group_id=?",
                (str(media_ref), str(persona_id), str(group_id)),
            ).fetchone()
        return row is not None

    async def media_data(self, media_ref: str) -> dict[str, str]:
        normalized = str(media_ref or "").strip()
        cached = self._read_cache(normalized)
        if cached is not None:
            return cached
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT kind, source_json, metadata_json FROM message_media WHERE media_ref=?",
                (normalized,),
            ).fetchone()
        if row is None:
            raise LookupError("media not found")
        kind = str(row["kind"])
        preview_kind = _PREVIEW_KINDS.get(kind)
        if preview_kind is None:
            raise LookupError("media has no preview")
        source = json.loads(str(row["source_json"]))
        metadata = json.loads(str(row["metadata_json"]))
        url = self._remote_source(source)
        if not url:
            raise LookupError("media source is not remotely previewable")
        maximum = _MAX_BYTES[preview_kind]
        fetched = self.fetcher(url, maximum)
        data, mime_type = await fetched if inspect.isawaitable(fetched) else fetched
        mime = str(mime_type or "").split(";", 1)[0].strip().lower()
        if mime not in _MIME_TYPES[preview_kind]:
            raise ValueError("unsupported media type")
        if not data or len(data) > maximum:
            raise ValueError("invalid media size")
        result = {
            "data_uri": "data:{};base64,{}".format(
                mime,
                base64.b64encode(data).decode("ascii"),
            ),
            "kind": preview_kind,
            "mime_type": mime,
            "name": str(metadata.get("name") or ""),
        }
        self._write_cache(normalized, result)
        return result

    def remember(self, event: SocialEventEnvelope) -> list[dict[str, object]]:
        segments = event.payload.get("segments")
        if not isinstance(segments, (list, tuple)):
            text = self._safe_text(event.payload.get("text"), 240)
            return [{"kind": "text", "text": text}] if text else []

        public_parts: list[dict[str, object]] = []
        for index, segment in enumerate(segments):
            if not isinstance(segment, Mapping):
                continue
            kind = str(segment.get("type") or "").strip().lower()
            data = segment.get("data")
            data_map = data if isinstance(data, Mapping) else {}
            if kind == "text":
                text = self._safe_text(data_map.get("text"), 240)
                if text:
                    public_parts.append({"kind": "text", "text": text})
                continue
            label = _LABELS.get(kind)
            if not label:
                continue
            part: dict[str, object] = {"kind": kind, "label": label}
            source = {
                key: str(data_map.get(key) or "").strip()
                for key in _SOURCE_KEYS
                if str(data_map.get(key) or "").strip()
            }
            if kind in _PREVIEW_KINDS and source:
                media_ref = self._media_ref(event, index, kind, source)
                part["media_ref"] = media_ref
                part["preview"] = _PREVIEW_KINDS[kind]
                self._store(event, media_ref, kind, source, data_map)
            name = self._public_name(kind, data_map)
            if name:
                part["name"] = name
            size = self._size(data_map.get("file_size"))
            if size is not None:
                part["size"] = size
            public_parts.append(part)
        return public_parts

    @staticmethod
    def summary(parts: object) -> str:
        if not isinstance(parts, (list, tuple)):
            return "[非文本消息]"
        labels: list[str] = []
        for part in parts:
            if not isinstance(part, Mapping):
                continue
            value = (
                str(part.get("text") or "").strip()
                if part.get("kind") == "text"
                else str(part.get("label") or "").strip()
            )
            if value:
                labels.append(value)
        return " · ".join(labels)[:240] or "[非文本消息]"

    def _store(
        self,
        event: SocialEventEnvelope,
        media_ref: str,
        kind: str,
        source: Mapping[str, str],
        data: Mapping[str, object],
    ) -> None:
        with connect_database(self.path) as db:
            db.execute(
                "INSERT INTO message_media(media_ref, persona_id, group_id, event_id, kind, "
                "source_json, metadata_json, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(media_ref) DO NOTHING",
                (
                    media_ref,
                    event.persona_id,
                    event.group_id,
                    event.event_id,
                    kind,
                    json.dumps(dict(source), ensure_ascii=False, sort_keys=True),
                    json.dumps(
                        {
                            "name": self._public_name(kind, data),
                            "size": self._size(data.get("file_size")),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    int(event.received_at),
                ),
            )

    def _ensure_table(self) -> None:
        with connect_database(self.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS message_media ("
                "media_ref TEXT PRIMARY KEY, persona_id TEXT NOT NULL, group_id TEXT NOT NULL, "
                "event_id TEXT NOT NULL, kind TEXT NOT NULL, source_json TEXT NOT NULL, "
                "metadata_json TEXT NOT NULL, created_at INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_message_media_scope "
                "ON message_media(persona_id, group_id, event_id)"
            )

    async def _fetch_url(self, url: str, maximum: int) -> tuple[bytes, str]:
        def read() -> tuple[bytes, str]:
            self._validate_remote_url(url)
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "Groupmate/1.0"},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                self._validate_remote_url(str(response.geturl()))
                return response.read(maximum + 1), str(response.headers.get_content_type())

        return await asyncio.to_thread(read)

    @classmethod
    def _validate_remote_url(cls, value: str) -> None:
        parsed = urllib.parse.urlsplit(str(value or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("media URL must use http or https")
        try:
            addresses = {
                item[4][0]
                for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)
            }
        except OSError as exc:
            raise ValueError("media host cannot be resolved") from exc
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError("media URL must resolve to a public address")

    @staticmethod
    def _remote_source(source: object) -> str:
        if not isinstance(source, Mapping):
            return ""
        for key in ("url", "file", "thumb"):
            value = str(source.get(key) or "").strip()
            if value.startswith(("https://", "http://")):
                return value
        return ""

    def _cache_path(self, media_ref: str) -> Path:
        key = hashlib.sha256(media_ref.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.json"

    def _read_cache(self, media_ref: str) -> dict[str, str] | None:
        if not media_ref:
            return None
        try:
            value = json.loads(self._cache_path(media_ref).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(value, Mapping):
            return None
        kind = str(value.get("kind") or "")
        mime = str(value.get("mime_type") or "")
        data_uri = str(value.get("data_uri") or "")
        if kind not in _MIME_TYPES or mime not in _MIME_TYPES[kind]:
            return None
        if not data_uri.startswith(f"data:{mime};base64,"):
            return None
        return {
            "data_uri": data_uri,
            "kind": kind,
            "mime_type": mime,
            "name": str(value.get("name") or ""),
        }

    def _write_cache(self, media_ref: str, value: Mapping[str, str]) -> None:
        if not media_ref:
            return
        path = self._cache_path(media_ref)
        temporary = path.with_suffix(".tmp")
        try:
            temporary.write_text(
                json.dumps(dict(value), ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError:
            pass

    @staticmethod
    def _media_ref(
        event: SocialEventEnvelope,
        index: int,
        kind: str,
        source: Mapping[str, str],
    ) -> str:
        value = json.dumps(
            [event.persona_id, event.group_id, event.event_id, index, kind, dict(source)],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"media:{hashlib.sha256(value.encode()).hexdigest()[:20]}"

    @classmethod
    def _safe_text(cls, value: object, limit: int) -> str:
        text = " ".join(str(value or "").split())
        text = _URL_RE.sub("[链接]", text)
        text = _LONG_NUMBER_RE.sub("[号码]", text)
        return text[:limit]

    @classmethod
    def _public_name(cls, kind: str, data: Mapping[str, object]) -> str:
        if kind == "image":
            return cls._safe_text(data.get("summary"), 80)
        if kind != "file":
            return cls._safe_text(data.get("name"), 80)
        candidate = str(data.get("name") or data.get("file") or "").strip()
        if not candidate or "://" in candidate or candidate.startswith(("/", "file:")):
            return ""
        return cls._safe_text(PurePath(candidate).name, 80)

    @staticmethod
    def _size(value: object) -> int | None:
        try:
            size = int(value)
        except (TypeError, ValueError):
            return None
        return size if 0 <= size <= 10 * 1024 * 1024 * 1024 else None


__all__ = ("MessageMediaDirectory",)
