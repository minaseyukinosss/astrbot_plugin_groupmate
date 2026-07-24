"""群消息 → 可读历史块（装配唯一消费点）。"""

from __future__ import annotations

import html
from typing import Optional, Sequence, Tuple

from ..models import ChatMessage
from .relationships import resolve_speaker

ACTIVE_CONTEXT_MAX_MESSAGES = 8
TOPIC_IDLE_GAP_SECONDS = 120
MERGE_WINDOW_SECONDS = 8


def select_active_messages(
    messages: Sequence[ChatMessage],
    *,
    topic_created_at: int = 0,
    max_messages: int = ACTIVE_CONTEXT_MAX_MESSAGES,
    idle_gap_seconds: int = TOPIC_IDLE_GAP_SECONDS,
) -> Tuple[ChatMessage, ...]:
    """Pick the current-topic slice for generation prompts."""
    if max_messages < 1:
        return ()
    candidates = tuple(messages)
    if topic_created_at > 0:
        candidates = tuple(
            message for message in candidates if message.timestamp >= topic_created_at
        )
    if not candidates:
        return ()

    selected = [candidates[-1]]
    for index in range(len(candidates) - 2, -1, -1):
        if len(selected) >= max_messages:
            break
        newer = selected[-1]
        older = candidates[index]
        if newer.timestamp - older.timestamp > idle_gap_seconds:
            break
        selected.append(older)
    selected.reverse()
    return tuple(selected)


def format_history_block(
    messages: Sequence[ChatMessage],
    relationships: dict,
    *,
    merge_window_seconds: int = MERGE_WINDOW_SECONDS,
) -> str:
    """Format active messages; merge same-sender bursts within a short window."""
    if not messages:
        return ""
    lines = []
    buffer_sender_id = ""
    buffer_speaker = ""
    buffer_rel = ""
    buffer_addr = ""
    buffer_parts: list = []
    buffer_ts = 0

    def flush() -> None:
        nonlocal buffer_sender_id, buffer_speaker, buffer_rel, buffer_addr, buffer_parts, buffer_ts
        if not buffer_parts:
            return
        content = " / ".join(buffer_parts)
        lines.append(
            '<message speaker="{}" relationship="{}" '
            'suggested_address="{}">{}</message>'.format(
                html.escape(buffer_speaker),
                html.escape(buffer_rel),
                html.escape(buffer_addr),
                html.escape(content[:400]),
            )
        )
        buffer_parts = []

    for message in messages:
        content = message.text or "[图片]"
        if message.image_urls and message.text:
            content += " [图片]"
        speaker, relationship, suggested_address = resolve_speaker(
            message.sender_id, message.sender_name, relationships
        )
        same = (
            buffer_parts
            and message.sender_id == buffer_sender_id
            and message.timestamp - buffer_ts <= merge_window_seconds
        )
        if same:
            buffer_parts.append(content[:300])
            buffer_ts = message.timestamp
            continue
        flush()
        buffer_sender_id = message.sender_id
        buffer_speaker = speaker
        buffer_rel = relationship
        buffer_addr = suggested_address
        buffer_parts = [content[:300]]
        buffer_ts = message.timestamp
    flush()
    return "\n".join(
        ["<recent_messages>", "\n".join(lines), "</recent_messages>"]
    )


def format_relationship_line(
    sender_id: str,
    sender_name: str,
    relationships: dict,
    favorability: Optional[int] = None,
) -> str:
    """Current-focus speaker relationship + favorability tier (no IDs)."""
    from .favorability import format_favorability_perception

    speaker, relationship, suggested_address = resolve_speaker(
        sender_id, sender_name, relationships
    )
    del speaker
    return format_favorability_perception(
        favorability,
        relationship=relationship,
        suggested_address=suggested_address,
    )


def focus_speaker(messages: Sequence[ChatMessage]) -> Tuple[str, str]:
    """Return (sender_id, sender_name) of the latest non-bot message."""
    for message in reversed(tuple(messages)):
        if message.is_bot:
            continue
        return message.sender_id, message.sender_name or ""
    if messages:
        latest = messages[-1]
        return latest.sender_id, latest.sender_name or ""
    return "", ""
