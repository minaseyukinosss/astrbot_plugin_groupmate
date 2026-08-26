"""Evidence-backed member profiles and group social graph."""

from .contracts import (
    MemberAlias,
    MemberIdentity,
    ProfileEpisode,
    ProfileFact,
    ProfileObservation,
    ProfileSnapshot,
    SocialEdge,
)
from .repository import ProfileIdentityConflict, ProfileRepository
from .identity import IdentityService

__all__ = (
    "MemberAlias",
    "MemberIdentity",
    "IdentityService",
    "ProfileEpisode",
    "ProfileFact",
    "ProfileIdentityConflict",
    "ProfileObservation",
    "ProfileRepository",
    "ProfileSnapshot",
    "SocialEdge",
)
