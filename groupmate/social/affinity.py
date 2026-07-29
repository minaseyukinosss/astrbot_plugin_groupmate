"""好感领域模型：连续状态只用于持久化，行为只读取离散档位。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..models import RelationshipState, StringEnum

AFFINITY_MIN = -100
AFFINITY_MAX = 100
FIRM_BOUNDARY_PRESSURE = 15


class AffinityBand(StringEnum):
    """AffinityBand（好感档位）。"""

    HOSTILE = "hostile"
    WARY = "wary"
    NEUTRAL = "neutral"
    FRIENDLY = "friendly"
    CLOSE = "close"


class ResponsePosture(StringEnum):
    """ResponsePosture（回应姿态）。"""

    FIRM = "firm"
    RESERVED = "reserved"
    POLITE = "polite"
    WARM = "warm"
    CLOSE = "close"


@dataclass(frozen=True)
class AffinitySnapshot:
    """AffinitySnapshot（好感快照），只暴露行为需要的离散信息。"""

    band: AffinityBand
    response_posture: ResponsePosture


def clamp_affinity(value: int) -> int:
    """clamp_affinity（好感限幅）：限制在 -100 至 100。"""

    return max(AFFINITY_MIN, min(AFFINITY_MAX, int(value)))


def band_for_affinity(value: int) -> AffinityBand:
    """band_for_affinity（好感值转档位）。"""

    score = clamp_affinity(value)
    if score <= -50:
        return AffinityBand.HOSTILE
    if score < 0:
        return AffinityBand.WARY
    if score < 30:
        return AffinityBand.NEUTRAL
    if score < 70:
        return AffinityBand.FRIENDLY
    return AffinityBand.CLOSE


def initial_affinity_for_relationship(relationship: str) -> int:
    """initial_affinity_for_relationship（配置关系初始好感）。"""

    key = str(relationship or "").strip()
    if key == "最亲近":
        return 80
    if key == "闺蜜":
        return 50
    return 0


def _posture_for_band(band: AffinityBand) -> ResponsePosture:
    return {
        AffinityBand.HOSTILE: ResponsePosture.FIRM,
        AffinityBand.WARY: ResponsePosture.RESERVED,
        AffinityBand.NEUTRAL: ResponsePosture.POLITE,
        AffinityBand.FRIENDLY: ResponsePosture.WARM,
        AffinityBand.CLOSE: ResponsePosture.CLOSE,
    }[band]


def snapshot_for_relationship(
    state: Optional[RelationshipState],
    *,
    configured_relationship: str = "",
) -> AffinitySnapshot:
    """snapshot_for_relationship（关系状态转好感快照）。"""

    if state is None:
        score = initial_affinity_for_relationship(configured_relationship)
        boundary_pressure = 0
    else:
        score = state.affinity
        boundary_pressure = state.boundary_pressure
    band = band_for_affinity(score)
    posture = _posture_for_band(band)
    if int(boundary_pressure) >= FIRM_BOUNDARY_PRESSURE:
        posture = ResponsePosture.FIRM
    return AffinitySnapshot(band=band, response_posture=posture)


__all__ = [
    "AFFINITY_MAX",
    "AFFINITY_MIN",
    "FIRM_BOUNDARY_PRESSURE",
    "AffinityBand",
    "AffinitySnapshot",
    "ResponsePosture",
    "band_for_affinity",
    "clamp_affinity",
    "initial_affinity_for_relationship",
    "snapshot_for_relationship",
]
