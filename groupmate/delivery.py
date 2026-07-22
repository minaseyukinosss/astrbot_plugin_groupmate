"""Humanized delivery planning: delay, segmentation, and expiry checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .models import Urgency


@dataclass(frozen=True)
class DeliveryPlan:
    decision_id: str
    group_id: str
    segments: Tuple[str, ...]
    delay_seconds: float
    expires_at: int
    quote_message_id: Optional[str] = None


_SENTENCE_END = re.compile(r"(?<=[。！？!?～~…])")


def split_reply_segments(
    text: str,
    max_chars: int = 60,
    max_segments: int = 2,
) -> Tuple[str, ...]:
    cleaned = (text or "").strip()
    if not cleaned:
        return ()
    max_chars = max(1, int(max_chars))
    max_segments = max(1, int(max_segments))
    if max_segments == 1:
        return (cleaned[:max_chars],)

    parts = [part.strip() for part in _SENTENCE_END.split(cleaned) if part.strip()]
    if len(parts) <= 1:
        return (cleaned[:max_chars],)

    segments: List[str] = []
    for part in parts:
        piece = part[:max_chars]
        if not piece:
            continue
        segments.append(piece)
        if len(segments) >= max_segments:
            break
    return tuple(segments) or (cleaned[:max_chars],)


def compute_delay_seconds(
    urgency: Urgency,
    text: str,
    *,
    enabled: bool = True,
    direct_wake: bool = False,
) -> float:
    if not enabled:
        return 0.0
    length = len((text or "").strip())
    if direct_wake or urgency is Urgency.HIGH:
        return min(0.35, 0.05 + length * 0.004)
    if urgency is Urgency.LOW:
        return min(1.5, 0.25 + length * 0.015)
    return min(0.9, 0.1 + length * 0.01)


def build_delivery_plan(
    *,
    decision_id: str,
    group_id: str,
    text: str,
    urgency: Urgency,
    now: int,
    ttl_seconds: int,
    max_chars: int,
    max_segments: int,
    humanize_delay: bool,
    direct_wake: bool,
    quote_message_id: Optional[str] = None,
) -> DeliveryPlan:
    segments = split_reply_segments(text, max_chars=max_chars, max_segments=max_segments)
    joined = "".join(segments)
    delay = compute_delay_seconds(
        urgency,
        joined,
        enabled=humanize_delay,
        direct_wake=direct_wake,
    )
    return DeliveryPlan(
        decision_id=decision_id,
        group_id=group_id,
        segments=segments,
        delay_seconds=delay,
        expires_at=int(now) + max(1, int(ttl_seconds)),
        quote_message_id=quote_message_id,
    )


def delivery_still_valid(plan: DeliveryPlan, now: int) -> bool:
    return bool(plan.segments) and int(now) <= int(plan.expires_at)
