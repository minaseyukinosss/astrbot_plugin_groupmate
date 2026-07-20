"""Convert locally reviewed shadow records into portable evaluation cases."""

from __future__ import annotations

import json
from pathlib import Path


def export_labeled_shadow_dataset(store, path: Path) -> int:
    rows = store.labeled_shadow_records()
    eligible = [row for row in rows if row.get("context_json")]
    if not eligible:
        raise ValueError("没有包含审阅上下文的已标注影子记录")
    group_aliases = {}
    output = []
    for row in eligible:
        group_hash = row["group_hash"]
        if group_hash not in group_aliases:
            group_aliases[group_hash] = "shadow-group-{}".format(len(group_aliases) + 1)
        group_id = group_aliases[group_hash]
        context = json.loads(row["context_json"])
        messages = []
        for index, item in enumerate(context, 1):
            message_id = "m{}".format(index)
            messages.append(
                {
                    "message_id": message_id,
                    "group_id": group_id,
                    "sender_id": item.get("sender", "成员"),
                    "sender_name": item.get("sender", "成员"),
                    "text": item.get("text", ""),
                    "timestamp": int(item.get("seconds_from_start", index - 1)),
                    "reply_to_message_id": (
                        "m{}".format(max(1, index - 1)) if item.get("reply") else None
                    ),
                    "mentions_bot": bool(item.get("mentions_bot", False)),
                    "reply_to_bot": bool(item.get("reply_to_bot", False)),
                    "is_command": bool(item.get("is_command", False)),
                    "is_bot": bool(item.get("is_bot", False)),
                    "image_urls": [],
                    "segment_types": list(item.get("segment_types", [])),
                }
            )
        output.append(
            {
                "schema_version": 1,
                "case_id": "shadow-{}".format(row["decision_id"]),
                "description": "插件本地影子标注场景",
                "messages": messages,
                "expected": {"label": row["label"]},
                "tags": ["shadow", "ordinary"],
                "source": "shadow_reviewed",
            }
        )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "".join(
            json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n" for case in output
        ),
        encoding="utf-8",
    )
    return len(output)
