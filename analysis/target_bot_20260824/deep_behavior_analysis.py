#!/usr/bin/env python3
"""Deep behavior cuts for the target bot and Groupmate strategy mapping."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import statistics
from collections import Counter
from pathlib import Path


BOT_UIN = "323537051"
BOT_UID = "u_wa9kVWFd1RzFlh_blskvQQ"
BOT_IDS = {BOT_UIN, BOT_UID}
BOT_NAMES = ("小维", "小真寻备用机", "小真寻")
NAME_PREFIX = re.compile(
    r"^\s*(?:小维|小真寻备用机|小真寻)"
    r"(?=$|[\s，,。.!！?？~～…]|呢|在|来|说|讲|帮|看|回|你|人|出|给|去|好|坏|别|要|能|会|可|真|是|有|没|发|唱|吃|喝|睡|醒|滚|爬|喵|酱)"
)
NAME_SUFFIX = re.compile(r"(?:小维|小真寻备用机|小真寻)[\s，,。.!！?？~～…]*$")
OBVIOUS_CAPABILITY = re.compile(
    r"^\s*(?:小维|小真寻备用机|小真寻)\s*"
    r"(?:来点|帮助|发言统计|发言榜|bq|xw|视频解析|解析视频)"
)
QUESTION = re.compile(r"[?？]|吗|呢|怎么|为什么|为何|能不能|是不是|有没有|可不可以|会不会")
AFFECTION = re.compile(r"喜欢|爱你|可爱|抱抱|亲亲|真棒|好棒|想你|结婚|老婆|妈妈|夸夸")
REQUEST = re.compile(r"帮|给|看看|看下|说说|告诉|去|做|发|来|叫|找|翻译|写|评价")
BARE_CALL = re.compile(
    r"^\s*(?:小维|小真寻备用机|小真寻)[\s，,。.!！?？~～…]*(?:在吗|呢|人呢)?[\s，,。.!！?？~～…]*$"
)


def sender(row: dict) -> dict:
    value = row.get("sender")
    return value if isinstance(value, dict) else {}


def sender_ids(row: dict) -> set[str]:
    value = sender(row)
    return {
        str(value.get(key) or "").strip()
        for key in ("uin", "uid")
        if str(value.get(key) or "").strip()
    }


def sender_aliases(row: dict) -> set[str]:
    value = sender(row)
    return {
        str(value.get(key) or "").strip()
        for key in ("uin", "uid", "name", "nickname", "groupCard")
        if str(value.get(key) or "").strip()
    }


def is_bot(row: dict) -> bool:
    return bool(sender_ids(row) & BOT_IDS)


def content(row: dict) -> dict:
    value = row.get("content")
    return value if isinstance(value, dict) else {}


def text_of(row: dict | None) -> str:
    if row is None:
        return ""
    return str(content(row).get("text") or "").strip()


def elements(row: dict) -> list[dict]:
    values = content(row).get("elements")
    return [item for item in values or [] if isinstance(item, dict)]


def mentions_bot(row: dict) -> bool:
    mentions = content(row).get("mentions")
    for mention in mentions or []:
        if not isinstance(mention, dict):
            continue
        values = {
            str(mention.get(key) or "").strip()
            for key in ("uin", "uid")
            if str(mention.get(key) or "").strip()
        }
        if values & BOT_IDS:
            return True
    return False


def reply_target(row: dict) -> str:
    for element in elements(row):
        if element.get("type") != "reply":
            continue
        data = element.get("data") if isinstance(element.get("data"), dict) else {}
        for key in ("senderUin", "senderName"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
    return ""


def classify_name_call(text: str) -> str:
    if OBVIOUS_CAPABILITY.search(text):
        return "能力请求"
    if BARE_CALL.search(text):
        return "纯呼唤"
    if QUESTION.search(text):
        return "提问"
    if AFFECTION.search(text):
        return "情绪/关系"
    if REQUEST.search(text):
        return "请求"
    return "陈述/评价"


def load_rows(export_dir: Path) -> list[dict]:
    rows_by_id: dict[str, dict] = {}
    for chunk in sorted((export_dir / "chunks").glob("*.jsonl")):
        with chunk.open(encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                row = json.loads(line)
                identity = str(row.get("id") or f"{chunk.name}:{index}")
                rows_by_id[identity] = row
    return sorted(
        rows_by_id.values(),
        key=lambda row: (int(row.get("timestamp") or 0), str(row.get("seq") or "")),
    )


def next_bot(rows: list[dict], index: int, seconds: int = 60) -> tuple[dict | None, float | None, int]:
    start = int(rows[index].get("timestamp") or 0)
    intervening = 0
    for candidate in rows[index + 1 :]:
        elapsed = int(candidate.get("timestamp") or 0) - start
        if elapsed > seconds * 1000:
            break
        if is_bot(candidate):
            return candidate, elapsed / 1000, intervening
        if not candidate.get("system"):
            intervening += 1
    return None, None, intervening


def linked_bot_reply(rows: list[dict], index: int, seconds: int = 60) -> tuple[dict | None, float | None]:
    start = int(rows[index].get("timestamp") or 0)
    caller_aliases = sender_aliases(rows[index])
    for candidate in rows[index + 1 :]:
        elapsed = int(candidate.get("timestamp") or 0) - start
        if elapsed > seconds * 1000:
            break
        if is_bot(candidate) and reply_target(candidate) in caller_aliases:
            return candidate, elapsed / 1000
    return None, None


def summarize(values: list[dict]) -> dict:
    next_latencies = [item["next_bot_seconds"] for item in values if item["next_bot_seconds"] is not None]
    linked_latencies = [item["linked_reply_seconds"] for item in values if item["linked_reply_seconds"] is not None]
    total = len(values)
    return {
        "messages": total,
        "next_bot_30s_count": sum(value <= 30 for value in next_latencies),
        "next_bot_30s_rate": round(sum(value <= 30 for value in next_latencies) / total, 6) if total else None,
        "next_bot_60s_count": len(next_latencies),
        "next_bot_60s_rate": round(len(next_latencies) / total, 6) if total else None,
        "median_next_bot_seconds": round(statistics.median(next_latencies), 2) if next_latencies else None,
        "linked_reply_60s_count": len(linked_latencies),
        "linked_reply_60s_rate": round(len(linked_latencies) / total, 6) if total else None,
        "median_linked_reply_seconds": round(statistics.median(linked_latencies), 2) if linked_latencies else None,
    }


def analyze(export_dir: Path, base_metrics_path: Path) -> dict:
    rows = load_rows(export_dir)
    call_rows: list[dict] = []
    for index, row in enumerate(rows):
        if is_bot(row) or row.get("system"):
            continue
        text = text_of(row)
        if mentions_bot(row):
            address_kind = "明确@"
        elif NAME_PREFIX.search(text):
            address_kind = "昵称开头"
        elif NAME_SUFFIX.search(text):
            address_kind = "昵称结尾"
        elif any(name in text for name in BOT_NAMES):
            address_kind = "正文提及"
        else:
            continue
        next_response, next_latency, intervening = next_bot(rows, index)
        linked_response, linked_latency = linked_bot_reply(rows, index)
        call_rows.append(
            {
                "time": row.get("time"),
                "address_kind": address_kind,
                "call_kind": classify_name_call(text) if address_kind in {"昵称开头", "昵称结尾"} else "—",
                "text": text[:160],
                "next_bot_seconds": next_latency,
                "linked_reply_seconds": linked_latency,
                "intervening_human_messages": intervening,
                "next_bot_text": text_of(next_response)[:160],
                "linked_reply_text": text_of(linked_response)[:160],
            }
        )

    by_address = {
        kind: summarize([row for row in call_rows if row["address_kind"] == kind])
        for kind in ("明确@", "昵称开头", "昵称结尾", "正文提及")
    }
    name_social = [
        row
        for row in call_rows
        if row["address_kind"] in {"昵称开头", "昵称结尾"}
        and row["call_kind"] != "能力请求"
    ]
    by_address["昵称直接社交（排除明显能力请求）"] = summarize(name_social)

    name_call_types = []
    for kind in ("能力请求", "纯呼唤", "提问", "情绪/关系", "请求", "陈述/评价"):
        values = [
            row
            for row in call_rows
            if row["address_kind"] in {"昵称开头", "昵称结尾"}
            and row["call_kind"] == kind
        ]
        name_call_types.append({"call_kind": kind, **summarize(values)})

    base = json.loads(base_metrics_path.read_text(encoding="utf-8"))
    trigger_rows = []
    for row in base["trigger_summary"]:
        trigger_rows.append(
            {
                "lane": row["trigger"],
                "turns": row["turns"],
                "share": row["share"],
                "median_latency_seconds": row["median_latency_seconds"],
                "p90_latency_seconds": row["p90_latency_seconds"],
                "explicit_followup_2m_rate": row["explicit_followup_2m_rate"],
                "median_text_chars": row["median_text_chars"],
            }
        )

    modality_rows = [
        {
            "modality": row["modality"],
            "elements": row["elements"],
            "message_share": row["message_share"],
        }
        for row in base["modalities"]
    ]

    social_examples = [
        row
        for row in call_rows
        if row["address_kind"] in {"昵称开头", "昵称结尾"}
        and row["call_kind"] != "能力请求"
        and row["linked_reply_seconds"] is not None
    ][:12]
    ignored_examples = [
        row
        for row in call_rows
        if row["address_kind"] in {"昵称开头", "昵称结尾"}
        and row["call_kind"] != "能力请求"
        and row["next_bot_seconds"] is None
    ][:12]

    return {
        "source": {
            "rows_after_dedup": len(rows),
            "observed_start": base["quality"]["observed_start_local"],
            "observed_end": base["quality"]["observed_end_local"],
            "bot_uin": BOT_UIN,
            "bot_uid": BOT_UID,
            "bot_display_name": base["bot"]["name"],
        },
        "addressing": by_address,
        "name_call_types": name_call_types,
        "trigger_lanes": trigger_rows,
        "turn_shape": base["turn_shape"],
        "style": base["style"],
        "modalities": modality_rows,
        "recipient_concentration": base["recipient_concentration"],
        "headline": {
            "name_prefix_30s_rate": by_address["昵称开头"]["next_bot_30s_rate"],
            "explicit_at_30s_rate": by_address["明确@"]["next_bot_30s_rate"],
            "continuation_share": next(
                row["share"] for row in trigger_rows if row["lane"] == "连续对话"
            ),
            "cold_open_share": next(
                row["share"] for row in trigger_rows if row["lane"] == "主动开场"
            ),
            "median_chain_turns": base["turn_shape"]["median_chain_turns"],
            "p90_chain_turns": base["turn_shape"]["p90_chain_turns"],
            "mean_messages_per_turn": base["turn_shape"]["mean_messages_per_turn"],
            "p90_messages_per_turn": base["turn_shape"]["p90_messages_per_turn"],
            "median_turn_chars": base["turn_shape"]["median_turn_chars"],
            "p90_turn_chars": base["turn_shape"]["p90_turn_chars"],
            "unique_reply_recipients": base["recipient_concentration"]["unique_reply_recipients"],
            "top_1_recipient_share": base["recipient_concentration"]["top_1_share"],
        },
        "social_examples": social_examples,
        "ignored_examples": ignored_examples,
        "quality": base["quality"],
        "interpretation_guardrails": {
            "next_bot_metric": "同一群中后续 30/60 秒内出现目标 Bot 发言，不能完全证明因果回复。",
            "linked_reply_metric": "仅统计导出中回复元素能解析到同一发送者的 Bot 回复，会低估未使用引用回复的真实响应。",
            "media_metric": "导出混合闲聊与插件能力，媒体占比不能直接作为闲聊核心的目标值。",
        },
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_sqlite(path: Path, result: dict) -> None:
    tables = {
        "trigger_lanes": result["trigger_lanes"],
        "addressing": [
            {"address_kind": label, **values}
            for label, values in result["addressing"].items()
        ],
        "call_types": result["name_call_types"],
        "headline": [result["headline"]],
    }
    with sqlite3.connect(path) as db:
        for table_name, rows in tables.items():
            db.execute(f'DROP TABLE IF EXISTS "{table_name}"')
            if not rows:
                continue
            fields = list(rows[0])
            declarations = []
            for field in fields:
                values = [row.get(field) for row in rows if row.get(field) is not None]
                sqlite_type = "REAL" if values and all(isinstance(value, (int, float)) for value in values) else "TEXT"
                declarations.append(f'"{field}" {sqlite_type}')
            db.execute(
                f'CREATE TABLE "{table_name}" ({", ".join(declarations)})'
            )
            placeholders = ", ".join("?" for _ in fields)
            db.executemany(
                f'INSERT INTO "{table_name}" VALUES ({placeholders})',
                [[row.get(field) for field in fields] for row in rows],
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("export_dir", type=Path)
    parser.add_argument("--base-metrics", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.export_dir, args.base_metrics)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(args.output.with_name("name_call_types.csv"), result["name_call_types"])
    write_sqlite(args.output.with_suffix(".sqlite3"), result)
    print(
        json.dumps(
            {
                "addressing": result["addressing"],
                "name_call_types": result["name_call_types"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
