"""Scene-and-act conditional metrics for offline behavior evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Sequence


@dataclass(frozen=True)
class BehaviorObservation:
    scene: str
    act: str
    replied: bool
    media: bool
    media_allowed: bool
    false_completion: bool
    duplicate_media: bool
    reply_chars: int
    latency_ms: int

    def __post_init__(self) -> None:
        scene = str(self.scene or "").strip()
        act = str(self.act or "").strip()
        if not scene or not act:
            raise ValueError("behavior observations require scene and act")
        object.__setattr__(self, "scene", scene)
        object.__setattr__(self, "act", act)
        object.__setattr__(self, "reply_chars", max(0, int(self.reply_chars)))
        object.__setattr__(self, "latency_ms", max(0, int(self.latency_ms)))


def _summarize(observations: Sequence[BehaviorObservation]) -> Dict[str, object]:
    opportunities = len(observations)
    replies = tuple(item for item in observations if item.replied)
    reply_count = len(replies)
    return {
        "opportunities": opportunities,
        "replies": reply_count,
        "reply_rate": reply_count / opportunities if opportunities else 0.0,
        "media_given_reply": (
            sum(bool(item.media) for item in replies) / reply_count
            if reply_count
            else 0.0
        ),
        "mean_reply_chars": (
            sum(item.reply_chars for item in replies) / reply_count
            if reply_count
            else 0.0
        ),
        "mean_latency_ms": (
            sum(item.latency_ms for item in replies) / reply_count
            if reply_count
            else 0.0
        ),
    }


def _grouped(observations, attribute):
    keys = sorted({getattr(item, attribute) for item in observations})
    return {
        key: _summarize(
            tuple(item for item in observations if getattr(item, attribute) == key)
        )
        for key in keys
    }


def aggregate_behavior_metrics(
    observations: Sequence[BehaviorObservation],
) -> Dict[str, object]:
    """Aggregate observations without producing runtime probability targets."""

    items = tuple(observations or ())
    if not all(isinstance(item, BehaviorObservation) for item in items):
        raise TypeError("observations must contain BehaviorObservation values")
    return {
        "by_scene": _grouped(items, "scene"),
        "by_act": _grouped(items, "act"),
        "violations": {
            "forbidden_media": sum(
                bool(item.media and not item.media_allowed) for item in items
            ),
            "false_completion": sum(
                bool(item.false_completion) for item in items
            ),
            "duplicate_media": sum(
                bool(item.duplicate_media) for item in items
            ),
        },
    }
