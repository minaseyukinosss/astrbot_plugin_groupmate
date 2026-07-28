"""Strict, read-only ingestion for QQChatExporter JSONL exports."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

from eval.shadow_models import ExportEvent, ExportSummary, IngestResult


_DECIMAL_SEQUENCE = re.compile(r"^[0-9]+$")
_MEDIA_MESSAGE_TYPES = frozenset(("video", "audio", "file", "forward"))


class ExportValidationError(ValueError):
    """Raised when an exporter manifest or record violates the ingest contract."""


def _required_mapping(value, name):
    if not isinstance(value, Mapping):
        raise ExportValidationError("{} must be a mapping".format(name))
    return value


def _required_nonnegative_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ExportValidationError(
            "{} must be a non-negative integer".format(name)
        )
    return value


def _required_string(value, name, nonempty=False):
    if not isinstance(value, str):
        suffix = "non-empty string" if nonempty else "string"
        raise ExportValidationError("{} must be a {}".format(name, suffix))
    if nonempty and not value.strip():
        raise ExportValidationError("{} must be a non-empty string".format(name))
    return value


def _optional_bool(raw, name):
    if name not in raw:
        return False
    value = raw[name]
    if not isinstance(value, bool):
        raise ExportValidationError("{} must be a boolean".format(name))
    return value


def _load_manifest(path):
    if not path.is_file():
        raise ExportValidationError("manifest.json is missing or is not a file")
    try:
        with path.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, UnicodeError) as exc:
        raise ExportValidationError(
            "manifest.json could not be read: {}".format(exc)
        ) from exc
    except ValueError as exc:
        raise ExportValidationError("manifest.json contains invalid JSON") from exc
    return _required_mapping(manifest, "manifest")


def _declared_chunks(root, manifest):
    statistics = _required_mapping(manifest.get("statistics"), "statistics")
    _required_nonnegative_int(
        statistics.get("totalMessages"), "statistics.totalMessages"
    )

    chunked = _required_mapping(manifest.get("chunked"), "chunked")
    if chunked.get("format") != "jsonl":
        raise ExportValidationError("chunked.format must be jsonl")
    raw_chunks = chunked.get("chunks")
    if not isinstance(raw_chunks, list) or not raw_chunks:
        raise ExportValidationError("chunked.chunks must be a non-empty list")

    chunks = []
    for index, raw_chunk in enumerate(raw_chunks):
        chunk_name = "chunked.chunks[{}]".format(index)
        chunk = _required_mapping(raw_chunk, chunk_name)
        relative = _required_string(
            chunk.get("relativePath"),
            "{}.relativePath".format(chunk_name),
        )
        count = _required_nonnegative_int(
            chunk.get("count"), "{}.count".format(chunk_name)
        )
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            raise ExportValidationError(
                "chunk path {} escapes export root".format(relative)
            )
        if not path.is_file():
            raise ExportValidationError(
                "chunk {} is missing or is not a file".format(relative)
            )
        chunks.append((path, count))
    return tuple(chunks)


def _parse_sequence(value):
    if isinstance(value, bool):
        raise ExportValidationError("seq must be a decimal string or integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _DECIMAL_SEQUENCE.match(value):
        return int(value)
    raise ExportValidationError("seq must be a decimal string or integer")


def _optional_reply_string(data, key, label):
    if key not in data:
        return ""
    value = data[key]
    if not isinstance(value, str):
        raise ExportValidationError("{} must be a string".format(label))
    return value.strip()


def _reply_fields(elements):
    for element_type, data in elements:
        if element_type != "reply":
            continue
        if "referencedMessageId" in data:
            reply_id = _optional_reply_string(
                data, "referencedMessageId", "reply referencedMessageId"
            )
        else:
            reply_id = _optional_reply_string(
                data, "messageId", "reply messageId"
            )
        if reply_id in ("", "0"):
            reply_id = ""
        sender_uin = _optional_reply_string(
            data, "senderUin", "reply senderUin"
        )
        return reply_id, sender_uin
    return "", ""


def _parse_mentions(content):
    if "mentions" not in content:
        return ()
    raw_mentions = content["mentions"]
    if not isinstance(raw_mentions, list):
        raise ExportValidationError("content.mentions must be a list")

    mentions = []
    seen = set()
    for index, raw_mention in enumerate(raw_mentions):
        label = "mention {}".format(index)
        mention = _required_mapping(raw_mention, label)
        found_identifier = False
        for key in ("uin", "uid"):
            if key not in mention:
                continue
            value = mention[key]
            if not isinstance(value, str):
                raise ExportValidationError(
                    "mention {} must be a string".format(key)
                )
            normalized = value.strip()
            if not normalized:
                continue
            found_identifier = True
            if normalized not in seen:
                seen.add(normalized)
                mentions.append(normalized)
        if not found_identifier:
            raise ExportValidationError(
                "mention must contain a non-empty uin or uid"
            )
    return tuple(mentions)


def _parse_resources(content):
    if "resources" not in content:
        return False
    raw_resources = content["resources"]
    if not isinstance(raw_resources, list):
        raise ExportValidationError("content.resources must be a list")

    has_image = False
    for index, raw_resource in enumerate(raw_resources):
        resource = _required_mapping(raw_resource, "resource {}".format(index))
        resource_type = _required_string(
            resource.get("type"), "resource type", nonempty=True
        ).strip()
        if resource_type == "image":
            has_image = True
    return has_image


def _parse_elements(content):
    raw_elements = content.get("elements")
    if not isinstance(raw_elements, list):
        raise ExportValidationError("content.elements must be a list")

    parsed = []
    text_parts = []
    for index, raw_element in enumerate(raw_elements):
        element = _required_mapping(raw_element, "element {}".format(index))
        element_type = _required_string(
            element.get("type"), "element type", nonempty=True
        ).strip()
        if "data" in element:
            data = _required_mapping(element["data"], "element data")
        else:
            data = {}
        parsed.append((element_type, data))
        if element_type == "text" and isinstance(data.get("text"), str):
            text_parts.append(data["text"])
    return tuple(parsed), "".join(text_parts)


def _parse_event(raw, chunk_name, line_number):
    location = "{}:{}".format(chunk_name, line_number)
    try:
        record = _required_mapping(raw, "record")
        message_id = _required_string(
            record.get("id"), "id", nonempty=True
        )
        timestamp = _required_nonnegative_int(
            record.get("timestamp"), "timestamp"
        )
        seq = _parse_sequence(record.get("seq"))

        sender = _required_mapping(record.get("sender"), "sender")
        sender_key = _required_string(
            sender.get("uid"), "sender.uid", nonempty=True
        )
        sender_uin = _required_string(sender.get("uin"), "sender.uin")
        sender_name = _required_string(sender.get("name"), "sender.name")
        message_type = _required_string(
            record.get("type"), "type", nonempty=True
        ).strip()

        content = _required_mapping(record.get("content"), "content")
        elements, text = _parse_elements(content)
        reply_id, reply_sender_uin = _reply_fields(elements)
        mentions = _parse_mentions(content)
        has_resource_image = _parse_resources(content)
        recalled = _optional_bool(record, "recalled")
        raw_system = _optional_bool(record, "system")
        element_types = tuple(item[0] for item in elements)

        return ExportEvent(
            message_id=message_id,
            seq=seq,
            timestamp_ms=timestamp,
            sender_key=sender_key,
            sender_uin=sender_uin,
            sender_name=sender_name,
            message_type=message_type,
            text=text,
            element_types=element_types,
            reply_to_message_id=reply_id,
            reply_to_sender_uin=reply_sender_uin,
            mentions=mentions,
            has_media=(
                "image" in element_types
                or has_resource_image
                or message_type in _MEDIA_MESSAGE_TYPES
            ),
            recalled=recalled,
            system=(
                raw_system
                or message_type == "system"
                or "system" in element_types
            ),
        )
    except ExportValidationError as exc:
        raise ExportValidationError("{} {}".format(location, exc)) from exc


def load_export(export_dir: Path, target_uin: str) -> IngestResult:
    """Load and strictly normalize one QQChatExporter export in memory."""

    root = Path(export_dir).expanduser().resolve()
    manifest = _load_manifest(root / "manifest.json")
    chunks = _declared_chunks(root, manifest)
    expected_total = manifest["statistics"]["totalMessages"]
    configured_target = str(target_uin)

    raw_by_id = {}
    events = []
    observed = 0
    duplicates = 0
    target_records = 0

    for chunk_path, declared_count in chunks:
        chunk_observed = 0
        try:
            with chunk_path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    observed += 1
                    chunk_observed += 1
                    try:
                        raw = json.loads(line)
                    except ValueError as exc:
                        raise ExportValidationError(
                            "{}:{} malformed JSON".format(
                                chunk_path.name, line_number
                            )
                        ) from exc

                    event = _parse_event(raw, chunk_path.name, line_number)
                    if event.sender_uin == configured_target:
                        target_records += 1
                    previous = raw_by_id.get(event.message_id)
                    if previous is not None:
                        if previous != raw:
                            raise ExportValidationError(
                                "conflicting duplicate message id {}".format(
                                    event.message_id
                                )
                            )
                        duplicates += 1
                        continue
                    raw_by_id[event.message_id] = raw
                    events.append(event)
        except ExportValidationError:
            raise
        except (OSError, UnicodeError) as exc:
            raise ExportValidationError(
                "chunk {} could not be read: {}".format(chunk_path.name, exc)
            ) from exc

        if chunk_observed != declared_count:
            raise ExportValidationError(
                "chunk record count mismatch: {} expected {} observed {}".format(
                    chunk_path.name, declared_count, chunk_observed
                )
            )

    if observed != expected_total:
        raise ExportValidationError(
            "manifest record count mismatch: expected {} observed {}".format(
                expected_total, observed
            )
        )
    if target_records == 0:
        raise ExportValidationError("configured target sender is absent")

    ordered = tuple(
        sorted(
            events,
            key=lambda item: (
                item.timestamp_ms,
                item.seq,
                item.message_id,
            ),
        )
    )
    return IngestResult(
        events=ordered,
        summary=ExportSummary(
            manifest_records=expected_total,
            observed_records=observed,
            target_records=target_records,
            excluded_system=sum(item.system for item in ordered),
            excluded_recalled=sum(item.recalled for item in ordered),
            duplicate_records=duplicates,
            chunk_count=len(chunks),
        ),
        target_uin=configured_target,
    )
