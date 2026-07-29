"""社会状态模块：好感档位、事件分类与关系投影。"""

from .affinity import (
    AffinityBand,
    AffinitySnapshot,
    ResponsePosture,
    band_for_affinity,
    clamp_affinity,
    initial_affinity_for_relationship,
    snapshot_for_relationship,
)
from .events import SocialEventClassifier
from .projector import SocialStateProjector

__all__ = [
    "AffinityBand",
    "AffinitySnapshot",
    "ResponsePosture",
    "SocialEventClassifier",
    "SocialStateProjector",
    "band_for_affinity",
    "clamp_affinity",
    "initial_affinity_for_relationship",
    "snapshot_for_relationship",
]
