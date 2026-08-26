"""Evidence-backed member profiles and group social graph."""

from .contracts import (
    MemberAlias,
    MemberIdentity,
    ProfileEpisode,
    ProfileCorrection,
    ProfileFact,
    ProfileFactCandidate,
    ProfileObservation,
    ProfileSnapshot,
    SocialEdge,
    SocialEdgeCandidate,
)
from .repository import ProfileIdentityConflict, ProfileRepository
from .identity import IdentityService
from .policy import ProfileEvidencePolicy
from .extractor import ProfileExtractionResult, ProfileExtractor
from .service import ProfileService
from .snapshot import SnapshotBuilder
from .graph import SocialGraph
from .group_portrait import GroupPortrait, GroupPortraitBuilder

__all__ = (
    "MemberAlias",
    "MemberIdentity",
    "IdentityService",
    "ProfileEpisode",
    "ProfileCorrection",
    "ProfileFact",
    "ProfileFactCandidate",
    "ProfileIdentityConflict",
    "ProfileObservation",
    "ProfileRepository",
    "ProfileEvidencePolicy",
    "ProfileExtractionResult",
    "ProfileExtractor",
    "ProfileSnapshot",
    "ProfileService",
    "SnapshotBuilder",
    "SocialGraph",
    "GroupPortrait",
    "GroupPortraitBuilder",
    "SocialEdge",
    "SocialEdgeCandidate",
)
