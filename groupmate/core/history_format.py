"""群消息 → 可读历史块（装配唯一消费点）。"""

from __future__ import annotations

import html
from typing import Optional, Sequence, Tuple

from ..models import ChatMessage, MessageOrigin, OutboundKind, OutboundSegment, RelationshipState
from ..social.affinity import AffinityBand, ResponsePosture, snapshot_for_relationship
from .relationships import resolve_speaker

ACTIVE_CONTEXT_MAX_MESSAGES = 8
TOPIC_IDLE_GAP_SECONDS = 120
MERGE_WINDOW_SECONDS = 8


def format_outbound_poke_note(target_user_id: str) -> str:
    """Short first-person-visible note that the character poked someone."""
    target = str(target_user_id or "").strip() or "某人"
    return "戳了戳 {}".format(target)


def compose_bot_delivery_text(
    outbound: Sequence[OutboundSegment],
    spoken_text: str = "",
) -> str:
    """Merge outbound poke actions with spoken text for short-term recall."""
    parts = []
    for item in tuple(outbound or ()):
        if not isinstance(item, OutboundSegment):
            continue
        if item.kind is OutboundKind.POKE and item.target_user_id:
            parts.append(format_outbound_poke_note(item.target_user_id))
    spoken = str(spoken_text or "").strip()
    if spoken:
        parts.append(spoken)
    return " / ".join(parts)


def _message_content(message: ChatMessage, *, character_name: str = "角色") -> str:
    if message.origin is MessageOrigin.SYSTEM_SYNTHETIC:
        kind = str(message.metadata.get("interaction_kind", "") or "")
        if kind == "poke":
            speaker = (message.sender_name or message.sender_id or "群友").strip() or "群友"
            role = str(message.metadata.get("poke_role", "") or "").lower()
            if role == "bystander":
                target = str(message.metadata.get("target_id", "") or "").strip()
                target_label = target or "某人"
                return "{} 戳了戳 {}".format(speaker, target_label)
            character = (character_name or "角色").strip() or "角色"
            return "{} 戳了戳 {}".format(speaker, character)
        return "[互动]"
    if (
        message.is_bot
        and "poke" in tuple(message.segment_types or ())
        and not (message.text or "").strip()
    ):
        target = str(message.metadata.get("poke_target_id", "") or "").strip() or "某人"
        speaker = (message.sender_name or character_name or "角色").strip() or "角色"
        return "{} 戳了戳 {}".format(speaker, target)
    content = message.text or "[图片]"
    if message.image_urls and message.text:
        content += " [图片]"
    return content


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
    character_name: str = "角色",
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
        content = _message_content(message, character_name=character_name)
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
    *,
    relationship_state: Optional[RelationshipState] = None,
    allow_intimate_address: bool = True,
) -> str:
    """format_relationship_line（关系行）：输出离散关系姿态，不输出分数。"""
    speaker, relationship, suggested_address = resolve_speaker(
        sender_id, sender_name, relationships
    )
    del speaker
    if not allow_intimate_address:
        suggested_address = ""
        relationship = relationship if relationship == "普通群友" else "普通群友"
    snapshot = snapshot_for_relationship(
        relationship_state,
        configured_relationship=relationship,
    )
    band_label = {
        AffinityBand.HOSTILE: "敌对",
        AffinityBand.WARY: "警惕",
        AffinityBand.NEUTRAL: "中性",
        AffinityBand.FRIENDLY: "友好",
        AffinityBand.CLOSE: "亲近",
    }[snapshot.band]
    posture_label = {
        ResponsePosture.FIRM: "坚定边界",
        ResponsePosture.RESERVED: "礼貌疏离",
        ResponsePosture.POLITE: "友好有分寸",
        ResponsePosture.WARM: "温暖松弛",
        ResponsePosture.CLOSE: "亲近柔和",
    }[snapshot.response_posture]
    parts = [
        "当前关系：" + html.escape(relationship),
        "好感状态：" + band_label,
        "回应姿态：" + posture_label,
    ]
    if suggested_address:
        parts.append("建议称呼：" + html.escape(suggested_address))
    parts.append("按关系证据保持分寸，不要复述内部状态")
    return "（" + "；".join(parts) + "。）"


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
