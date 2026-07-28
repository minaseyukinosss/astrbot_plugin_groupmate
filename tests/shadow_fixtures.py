import json
from pathlib import Path


def message(
    message_id,
    sender_uin,
    text,
    timestamp_ms,
    *,
    message_type="text",
    reply_to="",
    reply_sender_uin="",
    image=False,
    recalled=False
):
    elements = []
    if reply_to:
        elements.append(
            {
                "type": "reply",
                "data": {
                    "messageId": reply_to,
                    "referencedMessageId": reply_to,
                    "senderUin": reply_sender_uin,
                    "senderName": "Synthetic Replied User",
                    "content": "synthetic quoted content",
                    "timestamp": max(0, int(timestamp_ms / 1000) - 1),
                    "previewElements": [],
                },
            }
        )
    if text:
        elements.append({"type": "text", "data": {"text": text}})
    if image:
        elements.append(
            {
                "type": "image",
                "data": {
                    "filename": "synthetic-fixture.png",
                    "url": "/synthetic-download?fixture=image",
                },
            }
        )
    return {
        "id": str(message_id),
        "seq": str(timestamp_ms),
        "timestamp": int(timestamp_ms),
        "sender": {
            "uid": "synthetic-uid-{}".format(sender_uin),
            "uin": str(sender_uin),
            "name": "Synthetic User {}".format(sender_uin),
        },
        "type": message_type,
        "recalled": bool(recalled),
        "system": message_type == "system",
        "content": {
            "text": text,
            "elements": elements,
            "resources": [],
            "mentions": [],
        },
    }


def write_export(root, records, target_uin="20002", chunk_size=3):
    if (
        isinstance(chunk_size, bool)
        or not isinstance(chunk_size, int)
        or chunk_size <= 0
    ):
        raise ValueError("chunk_size must be a positive integer")
    root = Path(root)
    chunk_dir = root / "chunks"
    chunk_dir.mkdir(parents=True)
    chunks = []
    batches = [
        records[index : index + chunk_size]
        for index in range(0, len(records), chunk_size)
    ] or [[]]
    for index, batch in enumerate(batches, 1):
        relative = "chunks/chunk_{:04d}.jsonl".format(index)
        path = root / relative
        path.write_text(
            "".join(
                json.dumps(item, ensure_ascii=False) + "\n" for item in batch
            ),
            encoding="utf-8",
        )
        chunks.append({"relativePath": relative, "count": len(batch)})
    manifest = {
        "exporter": {"name": "QQChatExporter", "version": "5-test"},
        "statistics": {"totalMessages": len(records)},
        "target": {"uin": str(target_uin)},
        "chunked": {"format": "jsonl", "chunks": chunks},
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    return root
