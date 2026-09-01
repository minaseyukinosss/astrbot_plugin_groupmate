#!/usr/bin/env python3
"""Reproducible behavior profile for the target QQ group bot export."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo


LOCAL_TZ = ZoneInfo("Asia/Shanghai")
SESSION_GAP_SECONDS = 300
BOT_BURST_GAP_SECONDS = 90
AMBIENT_WINDOW_SECONDS = 120
CONTINUATION_WINDOW_SECONDS = 180
FOLLOWUP_WINDOW_SECONDS = 120
UTILITY_PATTERN = re.compile(
    r"当前智力|转人工|正在读取视频|视频内容如下|这个表情\s*bq|"
    r"数据更新成功|开始执行|插件|nonebot|解析(?:视频|链接)",
    re.IGNORECASE,
)
LAUGHTER_PATTERN = re.compile(r"哈哈|www+|笑死|绷|草(?:$|[！!。,.，])", re.IGNORECASE)
SOFT_PARTICLE_PATTERN = re.compile(r"[呢呀哦啦嘛喵]|~")


def quantile(values: Iterable[float], probability: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def safe_rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def plain_text(message: dict) -> str:
    content = message.get("content") if isinstance(message.get("content"), dict) else {}
    elements = content.get("elements") if isinstance(content.get("elements"), list) else []
    parts = []
    for element in elements:
        if not isinstance(element, dict) or element.get("type") != "text":
            continue
        data = element.get("data") if isinstance(element.get("data"), dict) else {}
        value = str(data.get("text") or "")
        if value:
            parts.append(value)
    if parts:
        return "".join(parts).strip()
    value = str(content.get("text") or "").strip()
    return re.sub(r"\[(?:图片|视频|语音|文件|回复消息)[^\]]*\]", "", value).strip()


def element_types(message: dict) -> tuple[str, ...]:
    content = message.get("content") if isinstance(message.get("content"), dict) else {}
    elements = content.get("elements") if isinstance(content.get("elements"), list) else []
    values = []
    for element in elements:
        if isinstance(element, dict):
            kind = str(element.get("type") or "").strip().lower()
            if kind:
                values.append(kind)
    return tuple(values)


def mention_ids(message: dict) -> set[str]:
    content = message.get("content") if isinstance(message.get("content"), dict) else {}
    mentions = content.get("mentions") if isinstance(content.get("mentions"), list) else []
    result: set[str] = set()
    for mention in mentions:
        if not isinstance(mention, dict):
            continue
        for key in ("uid", "uin"):
            value = str(mention.get(key) or "").strip()
            if value:
                result.add(value)
    return result


def reply_target(message: dict) -> str | None:
    content = message.get("content") if isinstance(message.get("content"), dict) else {}
    elements = content.get("elements") if isinstance(content.get("elements"), list) else []
    for element in elements:
        if not isinstance(element, dict) or element.get("type") != "reply":
            continue
        data = element.get("data") if isinstance(element.get("data"), dict) else {}
        for key in ("senderUin", "senderName"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
    return None


def message_sender_ids(message: dict) -> set[str]:
    sender = message.get("sender") if isinstance(message.get("sender"), dict) else {}
    return {
        value
        for value in (str(sender.get("uid") or "").strip(), str(sender.get("uin") or "").strip())
        if value
    }


def is_directed_at(message: dict, target_ids: set[str]) -> bool:
    return bool(mention_ids(message) & target_ids) or reply_target(message) in target_ids


def load_export(export_dir: Path) -> tuple[dict, list[dict], dict]:
    manifest_path = export_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    messages: list[dict] = []
    parse_errors = 0
    source_lines = 0
    original_timestamps: list[int] = []
    chunk_files = sorted((export_dir / "chunks").glob("*.jsonl"))
    for chunk_path in chunk_files:
        with chunk_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                source_lines += 1
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    parse_errors += 1
                    continue
                if not isinstance(message, dict):
                    parse_errors += 1
                    continue
                timestamp = int(message.get("timestamp") or 0)
                original_timestamps.append(timestamp)
                message["_source"] = f"{chunk_path.name}:{line_number}"
                message["_timestamp"] = timestamp
                messages.append(message)
    inversions = sum(
        current < previous
        for previous, current in zip(original_timestamps, original_timestamps[1:])
        if previous and current
    )
    messages.sort(key=lambda item: (item.get("_timestamp", 0), str(item.get("seq") or "")))
    quality = {
        "chunk_files": len(chunk_files),
        "source_lines": source_lines,
        "parsed_messages": len(messages),
        "parse_errors": parse_errors,
        "file_order_timestamp_inversions": inversions,
    }
    return manifest, messages, quality


def message_is_bot(message: dict, bot_ids: set[str]) -> bool:
    return bool(message_sender_ids(message) & bot_ids)


def assign_sessions(messages: list[dict]) -> dict[int, list[int]]:
    sessions: dict[int, list[int]] = defaultdict(list)
    session_id = 0
    previous_timestamp: int | None = None
    for index, message in enumerate(messages):
        timestamp = int(message["_timestamp"] / 1000)
        if previous_timestamp is None or timestamp - previous_timestamp > SESSION_GAP_SECONDS:
            session_id += 1
        message["_session_id"] = session_id
        message["_active_index"] = index
        sessions[session_id].append(index)
        previous_timestamp = timestamp
    return sessions


def build_bot_turns(active_messages: list[dict], bot_ids: set[str]) -> list[dict]:
    turns: list[dict] = []
    for index, message in enumerate(active_messages):
        if not message_is_bot(message, bot_ids):
            continue
        timestamp = int(message["_timestamp"] / 1000)
        if (
            turns
            and turns[-1]["end_index"] == index - 1
            and timestamp - turns[-1]["end_timestamp"] <= BOT_BURST_GAP_SECONDS
        ):
            turn = turns[-1]
            turn["messages"].append(message)
            turn["end_index"] = index
            turn["end_timestamp"] = timestamp
            continue
        turns.append(
            {
                "turn_id": len(turns) + 1,
                "session_id": message["_session_id"],
                "start_index": index,
                "end_index": index,
                "start_timestamp": timestamp,
                "end_timestamp": timestamp,
                "messages": [message],
            }
        )
    return turns


def classify_turns(active_messages: list[dict], turns: list[dict], bot_ids: set[str]) -> None:
    previous_turn: dict | None = None
    for turn in turns:
        start = turn["start_index"]
        end = turn["end_index"]
        session_id = turn["session_id"]
        previous_boundary = previous_turn["end_index"] + 1 if previous_turn else 0
        since_previous_bot = [
            message
            for message in active_messages[previous_boundary:start]
            if not message_is_bot(message, bot_ids)
        ]
        humans_in_session_before = [
            message
            for message in active_messages[:start]
            if message["_session_id"] == session_id and not message_is_bot(message, bot_ids)
        ]
        last_human = humans_in_session_before[-1] if humans_in_session_before else None
        latency = (
            turn["start_timestamp"] - int(last_human["_timestamp"] / 1000)
            if last_human is not None
            else None
        )
        bot_replies = [reply_target(message) for message in turn["messages"]]
        bot_replies = [target for target in bot_replies if target and target not in bot_ids]
        recent_inbound = [
            message
            for message in since_previous_bot
            if turn["start_timestamp"] - int(message["_timestamp"] / 1000) <= SESSION_GAP_SECONDS
        ]
        inbound_direct = any(is_directed_at(message, bot_ids) for message in recent_inbound)
        same_dialogue = bool(
            previous_turn
            and previous_turn["session_id"] == session_id
            and since_previous_bot
            and turn["start_timestamp"] - previous_turn["end_timestamp"] <= CONTINUATION_WINDOW_SECONDS
        )
        if inbound_direct:
            trigger = "被点名/被回复"
        elif bot_replies:
            trigger = "自主定向回复"
        elif same_dialogue:
            trigger = "连续对话"
        elif last_human is not None and latency is not None and latency <= AMBIENT_WINDOW_SECONDS:
            trigger = "环境话题加入"
        elif last_human is None:
            trigger = "主动开场"
        else:
            trigger = "延迟上下文"

        texts = [plain_text(message) for message in turn["messages"]]
        types = [kind for message in turn["messages"] for kind in element_types(message)]
        media_types = sorted({kind for kind in types if kind not in {"text", "at", "reply"}})
        next_bot_index = next(
            (candidate["start_index"] for candidate in turns if candidate["start_index"] > end),
            len(active_messages),
        )
        following_humans = [
            message
            for message in active_messages[end + 1 : next_bot_index]
            if not message_is_bot(message, bot_ids)
            and int(message["_timestamp"] / 1000) - turn["end_timestamp"] <= FOLLOWUP_WINDOW_SECONDS
        ]
        first_followup = following_humans[0] if following_humans else None
        explicit_followup = any(is_directed_at(message, bot_ids) for message in following_humans)

        turn.update(
            {
                "trigger": trigger,
                "latency_seconds": latency,
                "message_count": len(turn["messages"]),
                "text_chars": sum(len(text) for text in texts),
                "has_text": any(texts),
                "has_media": bool(media_types),
                "media_types": media_types,
                "uses_reply": any("reply" in element_types(message) for message in turn["messages"]),
                "uses_mention": any("at" in element_types(message) for message in turn["messages"]),
                "inbound_direct": inbound_direct,
                "bot_targeted_reply": bool(bot_replies),
                "utility_like": any(UTILITY_PATTERN.search(text) for text in texts),
                "any_followup_2m": bool(first_followup),
                "explicit_followup_2m": explicit_followup,
                "followup_seconds": (
                    int(first_followup["_timestamp"] / 1000) - turn["end_timestamp"]
                    if first_followup is not None
                    else None
                ),
                "reply_targets": bot_replies,
                "local_date": datetime.fromtimestamp(turn["start_timestamp"], LOCAL_TZ).date().isoformat(),
                "local_hour": datetime.fromtimestamp(turn["start_timestamp"], LOCAL_TZ).hour,
            }
        )
        previous_turn = turn


def summarize_trigger(trigger: str, trigger_turns: list[dict], total_turns: int) -> dict:
    latencies = [turn["latency_seconds"] for turn in trigger_turns if turn["latency_seconds"] is not None]
    return {
        "trigger": trigger,
        "turns": len(trigger_turns),
        "share": safe_rate(len(trigger_turns), total_turns),
        "median_latency_seconds": round(quantile(latencies, 0.5) or 0, 1) if latencies else None,
        "p90_latency_seconds": round(quantile(latencies, 0.9) or 0, 1) if latencies else None,
        "any_followup_2m_rate": safe_rate(sum(turn["any_followup_2m"] for turn in trigger_turns), len(trigger_turns)),
        "explicit_followup_2m_rate": safe_rate(
            sum(turn["explicit_followup_2m"] for turn in trigger_turns), len(trigger_turns)
        ),
        "median_text_chars": round(quantile([turn["text_chars"] for turn in trigger_turns], 0.5) or 0, 1),
    }


def build_analysis(export_dir: Path, bot_uin: str) -> dict:
    manifest, messages, quality = load_export(export_dir)
    bot_uid = next(
        (
            str(message.get("sender", {}).get("uid") or "")
            for message in messages
            if str(message.get("sender", {}).get("uin") or "") == bot_uin
        ),
        "",
    )
    bot_ids = {value for value in (bot_uin, bot_uid) if value}
    seen_ids: set[str] = set()
    analysis_messages = []
    for message in messages:
        message_id = str(message.get("id") or "")
        if message_id and message_id in seen_ids:
            continue
        if message_id:
            seen_ids.add(message_id)
        analysis_messages.append(message)
    active_messages = [
        message
        for message in analysis_messages
        if not bool(message.get("system"))
        and str(message.get("type") or "").lower() != "system"
        and isinstance(message.get("sender"), dict)
        and message_sender_ids(message)
        and message.get("_timestamp", 0) > 0
    ]
    sessions = assign_sessions(active_messages)
    turns = build_bot_turns(active_messages, bot_ids)
    classify_turns(active_messages, turns, bot_ids)

    ids = [str(message.get("id") or "") for message in messages]
    valid_ids = [value for value in ids if value]
    timestamps = [int(message.get("_timestamp") or 0) for message in messages if message.get("_timestamp")]
    manifest_stats = manifest.get("statistics") if isinstance(manifest.get("statistics"), dict) else {}
    manifest_range = manifest_stats.get("timeRange") if isinstance(manifest_stats.get("timeRange"), dict) else {}
    reply_messages = [message for message in messages if "reply" in element_types(message)]
    zero_reply_ids = 0
    for message in reply_messages:
        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        for element in content.get("elements") or []:
            if isinstance(element, dict) and element.get("type") == "reply":
                data = element.get("data") if isinstance(element.get("data"), dict) else {}
                if str(data.get("messageId") or "") in {"", "0"}:
                    zero_reply_ids += 1
                break

    bot_messages = [message for message in active_messages if message_is_bot(message, bot_ids)]
    human_messages = [message for message in active_messages if not message_is_bot(message, bot_ids)]
    bot_sender = next(message["sender"] for message in bot_messages)
    bot_texts = [plain_text(message) for message in bot_messages]
    nonempty_bot_texts = [text for text in bot_texts if text]
    normalized_texts = [re.sub(r"\s+", " ", text).strip().lower() for text in nonempty_bot_texts]
    text_counts = Counter(normalized_texts)
    repeated_message_count = sum(count for count in text_counts.values() if count > 1)

    trigger_groups: dict[str, list[dict]] = defaultdict(list)
    for turn in turns:
        trigger_groups[turn["trigger"]].append(turn)
    trigger_order = [
        "被点名/被回复",
        "自主定向回复",
        "连续对话",
        "环境话题加入",
        "主动开场",
        "延迟上下文",
    ]
    trigger_summary = [
        summarize_trigger(trigger, trigger_groups.get(trigger, []), len(turns))
        for trigger in trigger_order
        if trigger_groups.get(trigger)
    ]

    daily: dict[str, Counter] = defaultdict(Counter)
    for message in active_messages:
        date = datetime.fromtimestamp(int(message["_timestamp"] / 1000), LOCAL_TZ).date().isoformat()
        daily[date]["all_messages"] += 1
        if message_is_bot(message, bot_ids):
            daily[date]["bot_messages"] += 1
        else:
            daily[date]["human_messages"] += 1
    for turn in turns:
        daily[turn["local_date"]]["bot_turns"] += 1
    daily_rows = [
        {
            "date": date,
            **counts,
            "bot_message_share": safe_rate(counts["bot_messages"], counts["all_messages"]),
        }
        for date, counts in sorted(daily.items())
    ]

    session_rows = []
    for session_id, indices in sessions.items():
        session_messages = [active_messages[index] for index in indices]
        bot_count = sum(message_is_bot(message, bot_ids) for message in session_messages)
        session_turns = [turn for turn in turns if turn["session_id"] == session_id]
        session_rows.append(
            {
                "session_id": session_id,
                "messages": len(session_messages),
                "bot_messages": bot_count,
                "bot_turns": len(session_turns),
                "bot_participated": bool(bot_count),
                "bot_initiated": message_is_bot(session_messages[0], bot_ids),
            }
        )

    identity_names: dict[str, Counter] = defaultdict(Counter)
    for message in active_messages:
        sender = message.get("sender") or {}
        name = str(sender.get("name") or sender.get("groupCard") or sender.get("nickname") or "群成员")
        for identity in message_sender_ids(message):
            identity_names[identity][name] += 1
    for message in messages:
        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        for mention in content.get("mentions") or []:
            if not isinstance(mention, dict):
                continue
            name = str(mention.get("name") or "群成员")
            for key in ("uid", "uin"):
                identity = str(mention.get(key) or "").strip()
                if identity:
                    identity_names[identity][name] += 1
    uin_to_uid: dict[str, str] = {}
    for message in active_messages:
        sender = message.get("sender") or {}
        sender_uin = str(sender.get("uin") or "").strip()
        sender_uid = str(sender.get("uid") or "").strip()
        if sender_uin and sender_uid:
            uin_to_uid[sender_uin] = sender_uid
        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        for mention in content.get("mentions") or []:
            if not isinstance(mention, dict):
                continue
            mention_uin = str(mention.get("uin") or "").strip()
            mention_uid = str(mention.get("uid") or "").strip()
            if mention_uin and mention_uid:
                uin_to_uid[mention_uin] = mention_uid
    recipient_counts = Counter(
        uin_to_uid.get(target, target)
        for turn in turns
        for target in turn["reply_targets"]
    )
    recipients = [
        {
            "recipient_id": identity,
            "recipient_name": identity_names[identity].most_common(1)[0][0]
            if identity_names.get(identity)
            else "群成员",
            "reply_turns": count,
            "share": safe_rate(count, sum(recipient_counts.values())),
        }
        for identity, count in recipient_counts.most_common()
    ]

    message_element_counts = Counter(kind for message in bot_messages for kind in element_types(message))
    modality_rows = [
        {"modality": kind, "elements": count, "message_share": safe_rate(
            sum(kind in element_types(message) for message in bot_messages), len(bot_messages)
        )}
        for kind, count in message_element_counts.most_common()
    ]

    hourly: dict[int, Counter] = defaultdict(Counter)
    for message in active_messages:
        hour = datetime.fromtimestamp(int(message["_timestamp"] / 1000), LOCAL_TZ).hour
        if message_is_bot(message, bot_ids):
            hourly[hour]["bot_messages"] += 1
        else:
            hourly[hour]["human_messages"] += 1
    for turn in turns:
        hourly[turn["local_hour"]]["bot_turns"] += 1
    hourly_rows = [
        {
            "hour": hour,
            "human_messages": hourly[hour]["human_messages"],
            "bot_messages": hourly[hour]["bot_messages"],
            "bot_turns": hourly[hour]["bot_turns"],
            "turns_per_100_human_messages": round(
                hourly[hour]["bot_turns"] / hourly[hour]["human_messages"] * 100, 2
            )
            if hourly[hour]["human_messages"]
            else 0,
        }
        for hour in range(24)
    ]

    chain_lengths = []
    current_chain = 0
    for turn in turns:
        if turn["trigger"] == "连续对话":
            current_chain = current_chain + 1 if current_chain else 2
        else:
            if current_chain:
                chain_lengths.append(current_chain)
            current_chain = 0
    if current_chain:
        chain_lengths.append(current_chain)

    bot_turn_text_lengths = [turn["text_chars"] for turn in turns]
    observed_start = datetime.fromtimestamp(min(timestamps) / 1000, LOCAL_TZ).isoformat() if timestamps else None
    observed_end = datetime.fromtimestamp(max(timestamps) / 1000, LOCAL_TZ).isoformat() if timestamps else None
    observed_dates = sorted(daily)
    observed_date_values = [datetime.fromisoformat(value).date() for value in observed_dates]
    calendar_days = (
        (observed_date_values[-1] - observed_date_values[0]).days + 1
        if observed_date_values
        else 0
    )
    maximum_date_gap = max(
        (
            (current - previous).days - 1
            for previous, current in zip(observed_date_values, observed_date_values[1:])
        ),
        default=0,
    )
    top_three_dates_messages = sum(
        row["all_messages"] for row in sorted(daily_rows, key=lambda row: row["all_messages"], reverse=True)[:3]
    )
    quality.update(
        {
            "manifest_total_messages": int(manifest_stats.get("totalMessages") or 0),
            "manifest_count_matches": int(manifest_stats.get("totalMessages") or 0) == len(messages),
            "analyzed_messages_after_dedup": len(analysis_messages),
            "duplicate_message_ids": len(valid_ids) - len(set(valid_ids)),
            "missing_message_ids": len(messages) - len(valid_ids),
            "system_messages": sum(
                bool(message.get("system")) or str(message.get("type") or "").lower() == "system"
                for message in messages
            ),
            "missing_sender_identity": sum(not message_sender_ids(message) for message in messages),
            "observed_start_local": observed_start,
            "observed_end_local": observed_end,
            "manifest_start": manifest_range.get("start"),
            "manifest_end": manifest_range.get("end"),
            "reply_messages": len(reply_messages),
            "reply_messages_without_resolvable_message_id": zero_reply_ids,
            "reply_linkage_loss_rate": safe_rate(zero_reply_ids, len(reply_messages)),
            "observed_calendar_days": calendar_days,
            "days_with_messages": len(observed_dates),
            "days_without_messages_inside_range": max(0, calendar_days - len(observed_dates)),
            "maximum_gap_without_messages_days": maximum_date_gap,
            "top_three_dates_message_share": safe_rate(top_three_dates_messages, len(active_messages)),
        }
    )

    summary = {
        "group": manifest.get("chatInfo", {}).get("name"),
        "timezone": "Asia/Shanghai",
        "bot": {
            "uin": bot_uin,
            "uid": bot_uid,
            "name": bot_sender.get("name"),
            "nickname": bot_sender.get("nickname"),
        },
        "population": {
            "all_messages": len(messages),
            "active_messages": len(active_messages),
            "human_messages": len(human_messages),
            "bot_messages": len(bot_messages),
            "bot_message_share": safe_rate(len(bot_messages), len(active_messages)),
            "sessions_5m": len(session_rows),
            "sessions_with_bot": sum(row["bot_participated"] for row in session_rows),
            "session_participation_rate": safe_rate(
                sum(row["bot_participated"] for row in session_rows), len(session_rows)
            ),
            "bot_initiated_sessions": sum(row["bot_initiated"] for row in session_rows),
            "bot_initiated_session_rate": safe_rate(
                sum(row["bot_initiated"] for row in session_rows),
                sum(row["bot_participated"] for row in session_rows),
            ),
            "bot_turns": len(turns),
            "turns_per_100_human_messages": round(len(turns) / len(human_messages) * 100, 2)
            if human_messages
            else 0,
        },
        "turn_shape": {
            "mean_messages_per_turn": round(statistics.mean(turn["message_count"] for turn in turns), 2),
            "median_messages_per_turn": quantile([turn["message_count"] for turn in turns], 0.5),
            "p90_messages_per_turn": quantile([turn["message_count"] for turn in turns], 0.9),
            "multi_message_turn_rate": safe_rate(sum(turn["message_count"] > 1 for turn in turns), len(turns)),
            "median_turn_chars": quantile(bot_turn_text_lengths, 0.5),
            "p90_turn_chars": quantile(bot_turn_text_lengths, 0.9),
            "median_response_latency_seconds": quantile(
                [turn["latency_seconds"] for turn in turns if turn["latency_seconds"] is not None], 0.5
            ),
            "p90_response_latency_seconds": quantile(
                [turn["latency_seconds"] for turn in turns if turn["latency_seconds"] is not None], 0.9
            ),
            "media_turn_rate": safe_rate(sum(turn["has_media"] for turn in turns), len(turns)),
            "text_only_turn_rate": safe_rate(
                sum(turn["has_text"] and not turn["has_media"] for turn in turns), len(turns)
            ),
            "media_only_turn_rate": safe_rate(
                sum(turn["has_media"] and not turn["has_text"] for turn in turns), len(turns)
            ),
            "mixed_text_media_turn_rate": safe_rate(
                sum(turn["has_media"] and turn["has_text"] for turn in turns), len(turns)
            ),
            "reply_turn_rate": safe_rate(sum(turn["uses_reply"] for turn in turns), len(turns)),
            "utility_like_turn_rate": safe_rate(sum(turn["utility_like"] for turn in turns), len(turns)),
            "any_followup_2m_rate": safe_rate(sum(turn["any_followup_2m"] for turn in turns), len(turns)),
            "explicit_followup_2m_rate": safe_rate(
                sum(turn["explicit_followup_2m"] for turn in turns), len(turns)
            ),
            "multi_turn_dialogue_chains": len(chain_lengths),
            "median_chain_turns": quantile(chain_lengths, 0.5),
            "p90_chain_turns": quantile(chain_lengths, 0.9),
        },
        "style": {
            "text_messages": len(nonempty_bot_texts),
            "median_message_chars": quantile([len(text) for text in nonempty_bot_texts], 0.5),
            "p90_message_chars": quantile([len(text) for text in nonempty_bot_texts], 0.9),
            "short_message_rate_le_20_chars": safe_rate(
                sum(len(text) <= 20 for text in nonempty_bot_texts), len(nonempty_bot_texts)
            ),
            "question_rate": safe_rate(
                sum("?" in text or "？" in text for text in nonempty_bot_texts), len(nonempty_bot_texts)
            ),
            "laughter_rate": safe_rate(
                sum(bool(LAUGHTER_PATTERN.search(text)) for text in nonempty_bot_texts), len(nonempty_bot_texts)
            ),
            "soft_particle_rate": safe_rate(
                sum(bool(SOFT_PARTICLE_PATTERN.search(text)) for text in nonempty_bot_texts), len(nonempty_bot_texts)
            ),
            "repeat_phrase_coverage_rate": safe_rate(repeated_message_count, len(normalized_texts)),
            "exact_repeat_excess_rate": safe_rate(len(normalized_texts) - len(text_counts), len(normalized_texts)),
            "unique_text_ratio": safe_rate(len(text_counts), len(normalized_texts)),
            "top_repeated_texts": [
                {"text": text[:80], "count": count}
                for text, count in text_counts.most_common(15)
                if count > 1
            ],
        },
        "quality": quality,
        "trigger_summary": trigger_summary,
        "daily": daily_rows,
        "recipients": recipients,
        "recipient_concentration": {
            "reply_turns_with_resolved_target": sum(recipient_counts.values()),
            "unique_reply_recipients": len(recipient_counts),
            "top_1_share": safe_rate(
                sum(count for _, count in recipient_counts.most_common(1)), sum(recipient_counts.values())
            ),
            "top_5_share": safe_rate(
                sum(count for _, count in recipient_counts.most_common(5)), sum(recipient_counts.values())
            ),
            "top_10_share": safe_rate(
                sum(count for _, count in recipient_counts.most_common(10)), sum(recipient_counts.values())
            ),
        },
        "modalities": modality_rows,
        "hourly": hourly_rows,
        "definitions": {
            "session": f"相邻活跃消息间隔超过 {SESSION_GAP_SECONDS // 60} 分钟即开启新会话",
            "bot_turn": f"Bot 连续消息且间隔不超过 {BOT_BURST_GAP_SECONDS} 秒合并为一个发言轮次",
            "ambient": f"无明确召唤、距上一条群成员消息不超过 {AMBIENT_WINDOW_SECONDS} 秒",
            "followup": f"Bot 轮次结束后 {FOLLOWUP_WINDOW_SECONDS} 秒内、下一次 Bot 发言前的群成员消息",
        },
    }
    summary["_turns"] = turns
    return summary


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_outputs(analysis: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    turns = analysis.pop("_turns")
    (output_dir / "metrics.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_csv(
        output_dir / "turns.csv",
        turns,
        [
            "turn_id",
            "session_id",
            "start_timestamp",
            "end_timestamp",
            "local_date",
            "local_hour",
            "trigger",
            "latency_seconds",
            "message_count",
            "text_chars",
            "has_text",
            "has_media",
            "uses_reply",
            "uses_mention",
            "inbound_direct",
            "bot_targeted_reply",
            "utility_like",
            "any_followup_2m",
            "explicit_followup_2m",
            "followup_seconds",
        ],
    )
    write_csv(
        output_dir / "trigger_summary.csv",
        analysis["trigger_summary"],
        [
            "trigger",
            "turns",
            "share",
            "median_latency_seconds",
            "p90_latency_seconds",
            "any_followup_2m_rate",
            "explicit_followup_2m_rate",
            "median_text_chars",
        ],
    )
    write_csv(
        output_dir / "daily.csv",
        analysis["daily"],
        ["date", "all_messages", "human_messages", "bot_messages", "bot_turns", "bot_message_share"],
    )
    write_csv(
        output_dir / "recipients.csv",
        analysis["recipients"],
        ["recipient_id", "recipient_name", "reply_turns", "share"],
    )
    write_csv(
        output_dir / "hourly.csv",
        analysis["hourly"],
        ["hour", "human_messages", "bot_messages", "bot_turns", "turns_per_100_human_messages"],
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("export_dir", type=Path)
    parser.add_argument("--bot-uin", default="323537051")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    analysis = build_analysis(args.export_dir, args.bot_uin)
    write_outputs(analysis, args.output_dir)
    print(json.dumps({key: analysis[key] for key in ("bot", "population", "turn_shape", "style", "quality")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
