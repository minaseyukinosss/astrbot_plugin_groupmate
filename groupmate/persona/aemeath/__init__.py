"""爱弥斯 Persona Pack。"""

from pathlib import Path

from ...ports import GuardResult
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
    "GuardResult",
    "DEFAULT_RELATIONSHIPS",
    "RelationshipEntry",
    "parse_relationships",
]
