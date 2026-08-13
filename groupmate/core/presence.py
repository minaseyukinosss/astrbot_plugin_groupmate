"""群内 Presence 轻量投影（不落库）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..models import ChatMessage


@dataclass(frozen=True)
class PresenceProjection:
    recent_bot_density: float
    last_bot_at: int
    human_turn_gap: int
    human_message_count: int
    bot_message_count: int


def project_presence(
    messages: Sequence[ChatMessage],
    *,
    now: int,
    window: int = 8,
) -> PresenceProjection:
    recent = tuple(messages)[-max(1, int(window)) :]
    bot_count = 0
    last_bot_text = None
    for item in recent:
        if not item.is_bot:
            last_bot_text = None
            continue
        text = str(item.text or "").strip()
        if text != last_bot_text:
            bot_count += 1
        last_bot_text = text
    human_count = sum(1 for item in recent if not item.is_bot)
    density = float(bot_count) / float(len(recent)) if recent else 0.0
    last_bot_at = 0
    for item in reversed(recent):
        if item.is_bot:
            last_bot_at = int(item.timestamp)
            break
    gap = max(0, int(now) - last_bot_at) if last_bot_at else 10**9
    return PresenceProjection(
        recent_bot_density=density,
        last_bot_at=last_bot_at,
        human_turn_gap=gap,
        human_message_count=human_count,
        bot_message_count=bot_count,
    )
