"""Load custom-image bytes for sticker capture and chorus. Not a vision call."""

from __future__ import annotations

import ipaddress
import socket
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Mapping

from ..social_runtime.stickers.capture import image_facts
from ..social_runtime.stickers.contracts import MAX_STICKER_BYTES


def collect_local_sticker_files(
    payload: Mapping[str, object], *, max_bytes: int = MAX_STICKER_BYTES
) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for item in image_facts(payload):
        for key in ("file", "path", "url"):
            candidate = str(item.get(key) or "").strip()
            path = _local_path(candidate)
            if path is None or candidate in files:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size <= 0 or size > max_bytes:
                continue
            try:
                files[candidate] = path.read_bytes()
            except OSError:
                continue
    return files


def sticker_remote_urls(payload: Mapping[str, object]) -> tuple[str, ...]:
    urls: list[str] = []
    for item in image_facts(payload):
        for key in ("url", "file", "path"):
            value = str(item.get(key) or "").strip()
            if value.startswith(("https://", "http://")) and value not in urls:
                urls.append(value)
    return tuple(urls)


def alias_sticker_bytes(
    payload: Mapping[str, object], files: Mapping[str, bytes]
) -> dict[str, bytes]:
    aliased = dict(files)
    for item in image_facts(payload):
        keys = [
            str(item.get(field) or "").strip()
            for field in ("file", "path", "url")
            if str(item.get(field) or "").strip()
        ]
        shared = next((aliased[key] for key in keys if key in aliased), None)
        if shared is None:
            continue
        for key in keys:
            aliased.setdefault(key, shared)
    return aliased


def load_sticker_files(
    payload: Mapping[str, object], *, max_bytes: int = MAX_STICKER_BYTES
) -> dict[str, bytes]:
    files = alias_sticker_bytes(
        payload, collect_local_sticker_files(payload, max_bytes=max_bytes)
    )
    for url in sticker_remote_urls(payload):
        if url in files:
            continue
        try:
            files[url] = fetch_sticker_bytes(url, maximum=max_bytes)
        except (OSError, ValueError):
            continue
    return alias_sticker_bytes(payload, files)


def fetch_sticker_bytes(url: str, *, maximum: int = MAX_STICKER_BYTES) -> bytes:
    normalized = str(url or "").strip()
    parsed = _validate_sticker_url(normalized)
    request = urllib.request.Request(
        normalized,
        headers={
            "User-Agent": "Groupmate/1.0",
            "Referer": f"{parsed.scheme}://{parsed.netloc}/",
        },
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        _validate_sticker_url(str(response.geturl()))
        payload = response.read(int(maximum) + 1)
    if not payload or len(payload) > int(maximum):
        raise ValueError("sticker download is empty or too large")
    return payload


def _local_path(candidate: str) -> Path | None:
    value = str(candidate or "").strip()
    if value.startswith("file://"):
        parsed = urllib.parse.urlsplit(value)
        value = urllib.parse.unquote(parsed.path)
    path = Path(value)
    if path.is_absolute() is False or path.is_symlink() or not path.is_file():
        return None
    return path


def _validate_sticker_url(value: str) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(str(value or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("sticker URL must use http or https")
    try:
        addresses = {
            ipaddress.ip_address(item[4][0])
            for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)
        }
    except OSError as exc:
        raise ValueError("sticker host cannot be resolved") from exc
    if not addresses or any(
        not (address.is_global or address.is_loopback) for address in addresses
    ):
        raise ValueError("sticker URL host is not allowed")
    return parsed


__all__ = (
    "alias_sticker_bytes",
    "collect_local_sticker_files",
    "fetch_sticker_bytes",
    "load_sticker_files",
    "sticker_remote_urls",
)
