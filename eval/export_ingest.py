"""Translate chunked NTQQ exports into deterministic platform facts."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterator, Mapping

if "." in (__package__ or ""):
    from ..groupmate.social_runtime.contracts import SocialEventEnvelope
else:  # Repository-local offline evaluation entry point.
    from groupmate.social_runtime.contracts import SocialEventEnvelope


TARGET_BOT_QQ = "323537051"
DEFAULT_PERSONA_ID = "aemeath"
_MEDIA_TYPES = frozenset({"image", "video", "audio", "file"})
_EXPORT_DIRECTORY = re.compile(
    r"^group_(?P<group_id>\d{5,12})_\d{8}_\d{6}_chunked_jsonl$"
)


def _canonical_hash(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _record_timestamp_ms(record: Mapping[str, object]) -> int:
    value = int(record.get("timestamp") or 0)
    if value < 0:
        raise ValueError("export timestamp must not be negative")
    return value


def _manifest_path(path: str | Path) -> tuple[Path, Path]:
    candidate = Path(path)
    if candidate.is_dir():
        return candidate / "manifest.json", candidate
    return candidate, candidate.parent


def _chunk_paths(manifest: Mapping[str, object], root: Path) -> tuple[Path, ...]:
    chunked = manifest.get("chunked")
    if not isinstance(chunked, Mapping):
        raise ValueError("export manifest is missing chunk metadata")
    chunks = chunked.get("chunks")
    if not isinstance(chunks, list):
        raise ValueError("export manifest chunks must be a list")
    ordered = sorted(
        (item for item in chunks if isinstance(item, Mapping)),
        key=lambda item: int(item.get("index") or 0),
    )
    paths = []
    resolved_root = root.resolve()
    for item in ordered:
        relative = str(item.get("relativePath") or item.get("fileName") or "").strip()
        if not relative:
            raise ValueError("export chunk path must not be empty")
        chunk_path = (root / relative).resolve()
        if not chunk_path.is_relative_to(resolved_root):
            raise ValueError("export chunk path must stay inside export root")
        paths.append(chunk_path)
    return tuple(paths)


def _records(manifest: Mapping[str, object], root: Path) -> list[dict[str, object]]:
    records: list[tuple[int, int, dict[str, object]]] = []
    ordinal = 0
    for chunk_path in _chunk_paths(manifest, root):
        with chunk_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(
                        f"export record must be an object: {chunk_path.name}:{line_number}"
                    )
                records.append((_record_timestamp_ms(value), ordinal, value))
                ordinal += 1
    records.sort(key=lambda item: (item[0], item[1]))
    return [record for _, _, record in records]


def _elements(record: Mapping[str, object]) -> list[dict[str, object]]:
    content = record.get("content")
    if not isinstance(content, Mapping):
        return []
    value = content.get("elements")
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _element_data(element: Mapping[str, object]) -> dict[str, object]:
    value = element.get("data")
    return dict(value) if isinstance(value, Mapping) else {}


def _payload(
    record: Mapping[str, object],
    *,
    bot_qq: str,
    actor_id: str,
    timestamp_ms: int,
    known_message_ids: frozenset[str],
) -> tuple[dict[str, object], str | None]:
    elements = _elements(record)
    text_parts: list[str] = []
    mentions: list[str] = []
    media: list[dict[str, object]] = []
    reply_to: str | None = None
    reply_evidence: dict[str, object] | None = None
    for element in elements:
        kind = str(element.get("type") or "").strip()
        data = _element_data(element)
        if kind == "text":
            text_parts.append(str(data.get("text") or ""))
        elif kind == "reply" and reply_evidence is None:
            candidate = str(
                data.get("referencedMessageId") or data.get("messageId") or ""
            ).strip() or None
            resolved = bool(candidate and candidate in known_message_ids)
            reply_to = candidate if resolved else None
            reply_timestamp = data.get("timestamp")
            reply_evidence = {
                "resolved": resolved,
                "message_id": str(data.get("messageId") or "").strip() or None,
                "referenced_message_id": str(
                    data.get("referencedMessageId") or ""
                ).strip()
                or None,
                "sender_id": str(
                    data.get("senderUin") or data.get("senderUid") or ""
                ).strip()
                or None,
                "timestamp": (
                    int(reply_timestamp)
                    if reply_timestamp not in (None, "")
                    else None
                ),
                "content": str(data.get("content") or "").strip() or None,
            }
        elif kind == "at":
            mention = str(data.get("uin") or data.get("uid") or "").strip()
            if mention and mention not in mentions:
                mentions.append(mention)
        elif kind in _MEDIA_TYPES:
            media.append({"type": kind, **data})

    content = record.get("content")
    exported_text = (
        str(content.get("text") or "") if isinstance(content, Mapping) else ""
    )
    sender = record.get("sender")
    sender = dict(sender) if isinstance(sender, Mapping) else {}
    return (
        {
            "platform": "qq",
            "text": "".join(text_parts) or exported_text,
            "segments": elements,
            "reply_to": reply_to,
            "reply_evidence": reply_evidence,
            "mentions": mentions,
            "mentions_bot": bot_qq in mentions,
            "media": media,
            "sender": {
                "id": actor_id,
                "name": str(sender.get("groupCard") or sender.get("name") or ""),
            },
            "is_self": bool(actor_id and actor_id == bot_qq),
            "recalled": bool(record.get("recalled")),
            "system": bool(record.get("system"))
            or str(record.get("type") or "") == "system",
            "source_type": str(record.get("type") or ""),
            "source_timestamp_ms": timestamp_ms,
            "raw_evidence_hash": _canonical_hash(record),
        },
        reply_to,
    )


def ingest_export(
    path: str | Path,
    *,
    persona_id: str = DEFAULT_PERSONA_ID,
    bot_qq: str = TARGET_BOT_QQ,
) -> Iterator[SocialEventEnvelope]:
    """Yield deterministic envelopes from a chunked group export."""

    manifest_path, root = _manifest_path(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, Mapping):
        raise ValueError("export manifest must be an object")
    chat_info = manifest.get("chatInfo")
    if not isinstance(chat_info, Mapping) or chat_info.get("type") != "group":
        raise ValueError("evaluation ingest requires a group export")
    group_id = str(chat_info.get("peerUid") or "").strip()
    if not group_id:
        directory_match = _EXPORT_DIRECTORY.fullmatch(root.name)
        group_id = directory_match.group("group_id") if directory_match else ""
    if not group_id:
        raise ValueError("group export must identify its peer")

    records = _records(manifest, root)
    known_message_ids = frozenset(
        str(record.get("id") or "").strip()
        for record in records
        if str(record.get("id") or "").strip()
    )
    for record in records:
        timestamp_ms = _record_timestamp_ms(record)
        occurred_at = timestamp_ms // 1_000
        sender = record.get("sender")
        sender = dict(sender) if isinstance(sender, Mapping) else {}
        actor_id = str(sender.get("uin") or sender.get("uid") or "").strip()
        source_id = str(record.get("id") or "").strip()
        if not source_id:
            source_id = f"fingerprint:{_canonical_hash(record)}"
        event_id = f"qq:{source_id}"
        payload, reply_to = _payload(
            record,
            bot_qq=str(bot_qq),
            actor_id=actor_id,
            timestamp_ms=timestamp_ms,
            known_message_ids=known_message_ids,
        )
        event_type = (
            "platform.system"
            if payload["system"]
            else "platform.message"
        )
        yield SocialEventEnvelope.create(
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            received_at=occurred_at,
            persona_id=persona_id,
            group_id=group_id,
            actor_id=actor_id or None,
            source_message_id=source_id,
            correlation_id=event_id,
            causation_id=f"qq:{reply_to}" if reply_to else None,
            payload=payload,
        )


__all__ = ("DEFAULT_PERSONA_ID", "TARGET_BOT_QQ", "ingest_export")
