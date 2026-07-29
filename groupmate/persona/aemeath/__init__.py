"""爱弥斯 Persona Pack。"""

from pathlib import Path

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

PACK_DIR = Path(__file__).resolve().parent
CHARACTER_NAME = "爱弥斯"


def __getattr__(name):
    """按需加载生成侧组件，保持行为档案可被纯决策工具独立导入。"""

    if name == "GuardResult":
        from ...ports import GuardResult

        return GuardResult
    if name == "AemeathOutputFirewall":
        from .output_firewall import AemeathOutputFirewall

        return AemeathOutputFirewall
    if name == "AemeathPersonaProvider":
        from .provider import AemeathPersonaProvider

        return AemeathPersonaProvider
    raise AttributeError(name)

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
]
