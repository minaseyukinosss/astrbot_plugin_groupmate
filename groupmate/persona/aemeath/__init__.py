"""爱弥斯 Persona Pack。"""

from pathlib import Path

from ...ports import GuardResult
from .behavior_profile import (
    AEMEATH_PARTICIPATION_PROFILE,
    AffinityParticipationRule,
    BoundaryPolicy,
    ParticipationInhibition,
    ParticipationMotive,
    PersonaParticipationProfile,
    QuestionPolicy,
    RelationshipPolicy,
    TurnPolicy,
)
from .output_firewall import AemeathOutputFirewall
from .provider import AemeathPersonaProvider, CHARACTER_NAME
from .relationships import (
    DEFAULT_RELATIONSHIPS,
    RelationshipEntry,
    parse_relationships,
)

PACK_DIR = Path(__file__).resolve().parent

__all__ = [
    "PACK_DIR",
    "CHARACTER_NAME",
    "AemeathOutputFirewall",
    "AemeathPersonaProvider",
    "AEMEATH_PARTICIPATION_PROFILE",
    "AffinityParticipationRule",
    "BoundaryPolicy",
    "ParticipationInhibition",
    "ParticipationMotive",
    "PersonaParticipationProfile",
    "QuestionPolicy",
    "RelationshipPolicy",
    "TurnPolicy",
    "GuardResult",
    "DEFAULT_RELATIONSHIPS",
    "RelationshipEntry",
    "parse_relationships",
]
