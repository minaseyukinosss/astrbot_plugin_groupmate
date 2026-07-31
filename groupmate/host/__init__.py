"""宿主适配层。"""

from .bridge import AstrBotBridge, TurnOwner
from .config import (
    AstrBotConfigParser,
    ConfigDiagnostics,
    ConfigurationError,
    DeploymentSettings,
)
from .event_gate import HostEventDisposition, HostEventGate
from .ingress import AstrBotEventIngress
from .llm import (
    AstrBotGenerationModel,
    AstrBotPlatformPort,
    AstrBotVisionPort,
)
from .onebot import NapCatHistoryPort, OneBotTranslator

__all__ = [
    "AstrBotBridge",
    "AstrBotEventIngress",
    "TurnOwner",
    "AstrBotConfigParser",
    "ConfigDiagnostics",
    "ConfigurationError",
    "DeploymentSettings",
    "HostEventDisposition",
    "HostEventGate",
    "AstrBotGenerationModel",
    "AstrBotPlatformPort",
    "AstrBotVisionPort",
    "NapCatHistoryPort",
    "OneBotTranslator",
]
