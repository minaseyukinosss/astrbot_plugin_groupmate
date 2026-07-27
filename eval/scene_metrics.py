"""Scene-conditional replay metrics for humanlike group-chat behavior."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence

from groupmate.models import InteractionScene


@dataclass(frozen=True)
class SceneObservation:
    scene: InteractionScene
    replied: bool
    quoted: bool = False
    media: bool = False
    reply_chars: int = 0
    latency_seconds: Optional[float] = None


def _quantile(values, percentile):
    if not values:
        return None
    ordered = sorted(values)
    index = int((len(ordered) - 1) * float(percentile))
    return ordered[index]


def _summarize(observations: Sequence[SceneObservation]) -> Dict[str, object]:
    opportunities = len(observations)
    replies = [item for item in observations if item.replied]
    reply_count = len(replies)
    lengths = [max(0, int(item.reply_chars)) for item in replies]
    latencies = [
        max(0.0, float(item.latency_seconds))
        for item in replies
        if item.latency_seconds is not None
    ]
    return {
        "opportunities": opportunities,
        "replies": reply_count,
        "reply_rate": reply_count / opportunities if opportunities else 0.0,
        "quote_rate_given_reply": (
            sum(bool(item.quoted) for item in replies) / reply_count
            if reply_count
            else 0.0
        ),
        "media_rate_given_reply": (
            sum(bool(item.media) for item in replies) / reply_count
            if reply_count
            else 0.0
        ),
        "median_reply_chars": _quantile(lengths, 0.5),
        "p90_reply_chars": _quantile(lengths, 0.9),
        "median_latency_seconds": _quantile(latencies, 0.5),
        "p90_latency_seconds": _quantile(latencies, 0.9),
    }


def aggregate_scene_metrics(
    observations: Sequence[SceneObservation],
) -> Dict[str, Dict[str, object]]:
    """Aggregate behavior per scene; overall values are diagnostics only."""
    items = tuple(observations)
    result = {"overall": _summarize(items)}
    scenes = sorted({item.scene for item in items}, key=lambda scene: scene.value)
    for scene in scenes:
        result[scene.value] = _summarize(
            tuple(item for item in items if item.scene is scene)
        )
    return result
