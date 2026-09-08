"""Evidence-backed member profiles and group social graph."""

from .contracts import (
    MemberAlias,
    MemberIdentity,
    ProfileEpisode,
    ProfileCorrection,
    ProfileFact,
    ProfileFactCandidate,
    ProfileKnownCognition,
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
from .speech_style import (
    ImitationSession,
    MemberSpeechStyle,
    MemberStyleEvidence,
    MemberStyleEvidencePolicy,
    MemberStyleMaturity,
    MemberStyleSetting,
)
from .style_repository import MemberStyleRepository
from .snapshot import SnapshotBuilder
from .graph import SocialGraph
from .group_portrait import GroupPortrait, GroupPortraitBuilder
from .retrieval import ProfileRetrieval, ProfileRetriever, RetrievedMember

__all__ = (
    "MemberAlias",
    "MemberIdentity",
    "IdentityService",
    "ProfileEpisode",
    "ProfileCorrection",
    "ProfileFact",
    "ProfileFactCandidate",
    "ProfileKnownCognition",
    "ProfileIdentityConflict",
    "ProfileObservation",
    "ProfileRepository",
    "ProfileEvidencePolicy",
    "ProfileExtractionResult",
    "ProfileExtractor",
    "ProfileSnapshot",
    "ProfileService",
    "ImitationSession",
    "MemberSpeechStyle",
    "MemberStyleEvidence",
    "MemberStyleEvidencePolicy",
    "MemberStyleMaturity",
    "MemberStyleRepository",
    "MemberStyleSetting",
    "SnapshotBuilder",
    "SocialGraph",
    "GroupPortrait",
    "GroupPortraitBuilder",
    "ProfileRetrieval",
    "ProfileRetriever",
    "RetrievedMember",
    "SocialEdge",
    "SocialEdgeCandidate",
)
