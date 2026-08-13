"""社会状态模块：好感档位与已验证事件的关系投影。"""

from .affinity import (
    AffinityBand,
    AffinitySnapshot,
    ResponsePosture,
    band_for_affinity,
    clamp_affinity,
    initial_affinity_for_relationship,
    snapshot_for_relationship,
)
from .projector import SocialStateProjector
from .evidence import RelationshipEvidenceWriter
from .continuity import ContinuityWriter
from .commitments import SelfCommitmentWriter

__all__ = [
    "AffinityBand",
    "AffinitySnapshot",
    "ResponsePosture",
    "SocialStateProjector",
    "RelationshipEvidenceWriter",
    "ContinuityWriter",
    "SelfCommitmentWriter",
    "band_for_affinity",
    "clamp_affinity",
    "initial_affinity_for_relationship",
    "snapshot_for_relationship",
]
